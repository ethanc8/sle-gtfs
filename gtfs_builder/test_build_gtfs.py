import pytest

from gtfs_builder import build_gtfs as B


def row(arrival, departure=None, stop_id="X", seq=1):
    return {
        "stop_id": stop_id,
        "stop_sequence": seq,
        "arrival_text": arrival,
        "departure_text": departure or arrival,
        "track": "",
    }


def test_resolve_times_simple():
    rows = [row("5:46 A"), row("6:06 A"), row("6:51 A")]
    out = B.resolve_times(rows)
    assert [B.seconds_to_gtfs_time(r["arrival_seconds"]) for r in out] == ["05:46:00", "06:06:00", "06:51:00"]


def test_resolve_times_pivot_dwell():
    rows = [row("6:11 A"), row("6:51 A", "6:55 A"), row("7:00 A")]
    out = B.resolve_times(rows)
    assert B.seconds_to_gtfs_time(out[1]["arrival_seconds"]) == "06:51:00"
    assert B.seconds_to_gtfs_time(out[1]["departure_seconds"]) == "06:55:00"


def test_resolve_times_midnight_rollover():
    # trip 3699: New London 11:45 PM ... Old Saybrook 12:05 AM ... New Haven 12:50 AM
    rows = [row("11:45 P"), row("12:05 A"), row("12:09 A"), row("12:50 A")]
    out = B.resolve_times(rows)
    times = [B.seconds_to_gtfs_time(r["arrival_seconds"]) for r in out]
    assert times == ["23:45:00", "24:05:00", "24:09:00", "24:50:00"]
    # strictly non-decreasing, which is the whole point
    seconds = [r["arrival_seconds"] for r in out]
    assert seconds == sorted(seconds)


def test_calendar_active_on():
    cal = {
        "service_id": "SLE-20261005-weekend",
        "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0,
        "saturday": 1, "sunday": 1,
        "start_date": "2026-10-05", "end_date": "2026-11-01",
    }
    assert B.calendar_active_on(cal, B.parse_iso_date("2026-10-10")) is True  # Saturday
    assert B.calendar_active_on(cal, B.parse_iso_date("2026-10-11")) is True  # Sunday
    assert B.calendar_active_on(cal, B.parse_iso_date("2026-10-12")) is False  # Monday
    assert B.calendar_active_on(cal, B.parse_iso_date("2026-09-26")) is False  # before start_date


def test_suppressed_services_for():
    standing = [
        {
            "service_id": "SLE-20261005-weekday",
            "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1,
            "saturday": 0, "sunday": 0,
            "start_date": "2026-10-05", "end_date": "2026-11-01",
        },
        {
            "service_id": "SLE-20261005-weekend",
            "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0,
            "saturday": 1, "sunday": 1,
            "start_date": "2026-10-05", "end_date": "2026-11-01",
        },
    ]
    result = B.suppressed_services_for(["2026-10-10", "2026-10-11"], standing)
    assert result == [
        ("SLE-20261005-weekend", "20261010"),
        ("SLE-20261005-weekend", "20261011"),
    ]


def test_build_end_to_end_is_internally_consistent():
    config = B.load_config()
    tables = B.build(config)

    route_ids = {r["route_id"] for r in tables["routes"]}
    stop_ids = {s["stop_id"] for s in tables["stops"]}
    service_ids = {c["service_id"] for c in tables["calendar"]} | {c["service_id"] for c in tables["calendar_dates"]}

    assert len(tables["trips"]) > 0
    for trip in tables["trips"]:
        assert trip["route_id"] in route_ids
        assert trip["service_id"] in service_ids

    trip_ids = {t["trip_id"] for t in tables["trips"]}
    assert len(trip_ids) == len(tables["trips"]), "trip_id must be unique feed-wide"

    for st in tables["stop_times"]:
        assert st["trip_id"] in trip_ids
        assert st["stop_id"] in stop_ids

    # every exception "added" row must have a matching suppression of
    # whichever standing service covers that date (the project owner's
    # requirement, re-checked here independently of validator/'s Rust check)
    added = [(c["service_id"], c["date"]) for c in tables["calendar_dates"] if c["exception_type"] == 1]
    removed = {(c["service_id"], c["date"]) for c in tables["calendar_dates"] if c["exception_type"] == 2}
    for service_id, date in added:
        for cal in tables["calendar"]:
            if cal["service_id"] == service_id:
                continue
            if B.calendar_active_on(
                {**cal, "start_date": f"{cal['start_date'][:4]}-{cal['start_date'][4:6]}-{cal['start_date'][6:]}",
                 "end_date": f"{cal['end_date'][:4]}-{cal['end_date'][4:6]}-{cal['end_date'][6:]}"},
                B.parse_iso_date(f"{date[:4]}-{date[4:6]}-{date[6:]}"),
            ):
                assert (cal["service_id"], date) in removed
