"""Build the GTFS feed from gtfs_builder/config.toml + extracted/*.csv.

See llm_notes/SPEC.md sections 0, 4-6, 9 for the reasoning. Summary of the
two revision types (config.toml `[[revision]]`):

- "standing": an ordinary day-of-week calendar.txt service over a date
  range (e.g. the Mon-Fri pattern effective 2026-03-29..2026-10-04).
- "exception": a calendar_dates.txt-only service active on exactly the
  listed `dates`. Per the project owner, every exception revision is a
  COMPLETELY SEPARATE calendar service from any standing revision: its
  trips get their own trip_ids and stop_times (never shared with a standing
  revision's trips, even when the printed schedule is identical -- see the
  20261010-11-outage revision, whose SLE trips/times are byte-identical to
  the standing 20261005-weekend revision but are still built as fully
  independent GTFS trips), and the standing service that would otherwise
  run on those date(s) is suppressed via calendar_dates.txt exception_type=2
  rows (`suppressed_services_for`).
"""

import argparse
import csv
import datetime
import sys
import tomllib
import zipfile
from pathlib import Path

from gtfs_builder.pdf_extract import TIME_RE

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"
EXTRACTED_DIR = REPO_ROOT / "extracted"

WEEKDAY_FIELDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_TYPE_FLAGS = {
    "weekday": {"monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1, "saturday": 0, "sunday": 0},
    "weekend": {"monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0, "saturday": 1, "sunday": 1},
}


def load_config() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def to_gtfs_date(iso_date: str) -> str:
    return iso_date.replace("-", "")


def parse_iso_date(iso_date: str) -> datetime.date:
    return datetime.date.fromisoformat(iso_date)


def load_extracted(revision_name: str) -> list[dict]:
    path = EXTRACTED_DIR / f"{revision_name}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def resolve_times(rows: list[dict]) -> list[dict]:
    """Attach arrival_seconds/departure_seconds to each stop_time row
    (already in trip/stop_sequence order), applying GTFS's past-midnight
    convention (hour >= 24) whenever a time is earlier than the previous
    one -- these timetables print some very-late trips whose last couple of
    stops are technically the next calendar day (e.g. trip 3699 departs
    New London 11:45 PM and reaches New Haven at 12:50 AM)."""
    out = []
    rollover = 0
    prev_total = -1
    for row in rows:
        arr_h, arr_m = parse_time_text(row["arrival_text"])
        arr_total = arr_h * 60 + arr_m
        if arr_total + rollover * 1440 < prev_total:
            rollover += 1
        arr_total += rollover * 1440
        prev_total = arr_total

        dep_h, dep_m = parse_time_text(row["departure_text"])
        dep_total = dep_h * 60 + dep_m
        if dep_total + rollover * 1440 < prev_total:
            rollover += 1
        dep_total += rollover * 1440
        prev_total = dep_total

        out.append(
            {
                **row,
                "arrival_seconds": arr_total * 60,
                "departure_seconds": dep_total * 60,
            }
        )
    return out


def parse_time_text(text: str) -> tuple[int, int]:
    m = TIME_RE.match(text.strip())
    if not m:
        raise ValueError(f"unparseable time cell {text!r}")
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm == "P" and hour != 12:
        hour += 12
    if ampm == "A" and hour == 12:
        hour = 0
    return hour, minute


def seconds_to_gtfs_time(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def calendar_active_on(cal_row: dict, date: datetime.date) -> bool:
    if not (parse_iso_date(cal_row["start_date"]) <= date <= parse_iso_date(cal_row["end_date"])):
        return False
    return cal_row[WEEKDAY_FIELDS[date.weekday()]] == 1


def suppressed_services_for(exception_dates: list[str], standing_calendars: list[dict]) -> list[tuple[str, str]]:
    """[(service_id, date), ...] exception_type=2 rows: every standing
    service that would otherwise be active on one of these dates."""
    out = []
    for iso_date in exception_dates:
        date = parse_iso_date(iso_date)
        for cal in standing_calendars:
            if calendar_active_on(cal, date):
                out.append((cal["service_id"], to_gtfs_date(iso_date)))
    return out


def build(config: dict) -> dict[str, list[dict]]:
    agency = [config["agency"]]
    routes = [
        {
            "route_id": config["route"]["route_id"],
            "route_short_name": config["route"]["route_short_name"],
            "route_long_name": config["route"]["route_long_name"],
            "route_type": config["route"]["route_type"],
            "route_color": config["route"]["route_color"],
            "route_text_color": config["route"]["route_text_color"],
        }
    ]
    stops = [
        {
            "stop_id": s["stop_id"],
            "stop_name": s["stop_name"],
            "stop_lat": s["stop_lat"],
            "stop_lon": s["stop_lon"],
            "location_type": 0,
            "wheelchair_boarding": 1,  # every SLE station has step-free access
        }
        for s in config["stops"]
    ]
    headsign_by_stop_id = {s["stop_id"]: s["stop_name"] for s in config["stops"]}
    feed_info = [
        {
            **config["feed_info"],
            "feed_start_date": to_gtfs_date(config["feed_info"]["feed_start_date"]),
            "feed_end_date": to_gtfs_date(config["feed_info"]["feed_end_date"]),
        }
    ]

    calendar = []
    calendar_dates = []
    trips = []
    stop_times = []

    standing_calendars = []
    for rev in config["revision"]:
        if rev["type"] != "standing":
            continue
        service_id = f"SLE-{rev['name']}"
        cal_row = {
            "service_id": service_id,
            **DAY_TYPE_FLAGS[rev["day_type"]],
            "start_date": to_gtfs_date(rev["start_date"]),
            "end_date": to_gtfs_date(rev["end_date"]),
        }
        calendar.append(cal_row)
        # keep ISO dates around too, for calendar_active_on()
        standing_calendars.append({**cal_row, "start_date": rev["start_date"], "end_date": rev["end_date"]})

    for rev in config["revision"]:
        if rev["type"] != "exception":
            continue
        service_id = f"SLE-{rev['name']}"
        for iso_date in rev["dates"]:
            calendar_dates.append(
                {"service_id": service_id, "date": to_gtfs_date(iso_date), "exception_type": 1}
            )
        for suppressed_service_id, gtfs_date in suppressed_services_for(rev["dates"], standing_calendars):
            calendar_dates.append(
                {"service_id": suppressed_service_id, "date": gtfs_date, "exception_type": 2}
            )

    for rev in config["revision"]:
        service_id = f"SLE-{rev['name']}"
        extracted_rows = load_extracted(rev["name"])
        by_trip: dict[str, list[dict]] = {}
        for row in extracted_rows:
            by_trip.setdefault(row["trip_short_name"], []).append(row)

        for trip_short_name, rows in by_trip.items():
            rows.sort(key=lambda r: int(r["stop_sequence"]))
            trip_id = f"SLE-{rev['name']}-{trip_short_name}"
            direction_id = int(rows[0]["direction_id"])
            trips.append(
                {
                    "route_id": config["route"]["route_id"],
                    "service_id": service_id,
                    "trip_id": trip_id,
                    "trip_headsign": headsign_by_stop_id[rows[-1]["stop_id"]],
                    "trip_short_name": trip_short_name,
                    "direction_id": direction_id,
                    "bikes_allowed": 1,
                }
            )
            resolved = resolve_times(rows)
            for row in resolved:
                stop_times.append(
                    {
                        "trip_id": trip_id,
                        "arrival_time": seconds_to_gtfs_time(row["arrival_seconds"]),
                        "departure_time": seconds_to_gtfs_time(row["departure_seconds"]),
                        "stop_id": row["stop_id"],
                        "stop_sequence": row["stop_sequence"],
                        "pickup_type": 0,
                        "drop_off_type": 0,
                        "track": row["track"],
                    }
                )

    return {
        "agency": agency,
        "routes": routes,
        "stops": stops,
        "calendar": calendar,
        "calendar_dates": calendar_dates,
        "trips": trips,
        "stop_times": stop_times,
        "feed_info": feed_info,
    }


FIELDNAMES = {
    "agency": ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_phone", "agency_lang"],
    "routes": ["route_id", "route_short_name", "route_long_name", "route_type", "route_color", "route_text_color"],
    "stops": ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "wheelchair_boarding"],
    "calendar": ["service_id", *WEEKDAY_FIELDS, "start_date", "end_date"],
    "calendar_dates": ["service_id", "date", "exception_type"],
    "trips": ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name", "direction_id", "bikes_allowed"],
    "stop_times": ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "pickup_type", "drop_off_type", "track"],
    "feed_info": ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"],
}


def write_gtfs(tables: dict[str, list[dict]], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fieldnames in FIELDNAMES.items():
        path = out_dir / f"{name}.txt"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in tables[name]:
                writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_zip(out_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FIELDNAMES:
            zf.write(out_dir / f"{name}.txt", arcname=f"{name}.txt")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "build" / "gtfs"))
    parser.add_argument("--zip", default=str(REPO_ROOT / "build" / "sle-gtfs.zip"))
    args = parser.parse_args()

    config = load_config()
    try:
        tables = build(config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        print("have you run `make extract-draft` and promoted the drafts?", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    write_gtfs(tables, out_dir)
    write_zip(out_dir, Path(args.zip))

    for name, rows in tables.items():
        print(f"  {name}.txt: {len(rows)} rows")
    print(f"wrote {args.out_dir} and {args.zip}")


if __name__ == "__main__":
    main()
