"""Extract SLE timetables from a source PDF into draft CSVs.

See llm_notes/SPEC.md sections 2, 7, 9 for the reasoning. Quick summary of
the page layout every source PDF (and every older/holiday example) shares:

    Effective <date[s]>[, <year>] [ONLY]
    <day-type header>                      e.g. "MONDAY TO FRIDAY | LUNES A VIERNES"
    TO NEW HAVEN                           New London -> New Haven table
    CONNECTIONS TO NEW YORK (DEPARTING)    MNR columns (dropped) + SLE THRU column
    CONNECTIONS FROM NEW YORK (ARRIVING)   MNR columns (dropped) + SLE THRU column
    TO NEW LONDON                          New Haven -> New London table

A 2-page "standing" PDF repeats this once per page (page 0 = weekday, page 1
= weekend). A 1-page "exception" PDF has exactly one such block.

Each table is a true ruled grid (see pdf_grid.py) whose header row (row 0)
reads like "SLE THRU 1633", "SLE 1637", "AMTK 93*", or "MNR 1537" -- we keep
only "SLE"/"SLE THRU" columns (requirement #8 drops AMTK and MNR). The
"SLE THRU" column in a CONNECTIONS table is the New Haven<->Stamford leg of a
thru-trip, not an MNR train, and must still be kept even though every other
column in that same table is dropped.

Track badges (the "TRACK n" raster images) are associated with a cell by
bounding-box position, exactly like a text cell, then OCR'd -- see
`extract_track_badges`.
"""

import argparse
import csv
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pdfplumber
import pytesseract
from PIL import Image

from gtfs_builder import pdf_grid

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "sources"
CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"
EXTRACTED_DIR = REPO_ROOT / "extracted"

SECTION_HEADERS = {
    "TO NEW HAVEN",
    "CONNECTIONS TO NEW YORK (DEPARTING)",
    "CONNECTIONS FROM NEW YORK (ARRIVING)",
    "TO NEW LONDON",
}
DAY_TYPE_PREFIXES = (
    "MONDAY TO FRIDAY",
    "WEEKEND/HOLIDAY",
    "SATURDAY AND SUNDAY",
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)

# Some tables' header row is set with enough letter-spacing that pdfplumber
# reads each glyph as its own "word" (e.g. "SLE T H RU 16 3 3" instead of
# "SLE THRU 1633") -- so classification strips all whitespace first and
# matches against a space-free pattern, rather than relying on there being
# exactly one space between tokens.
COLUMN_HEADER_RE = re.compile(r"^(SLETHRU|SLE|AMTK|MNR)(\d+\*?)")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([AP])$")
# A single digit, not a run of digits: the badge's arrow glyph is frequently
# misread by tesseract as an extra trailing digit directly butted up against
# the real one with no separating space (e.g. a "TRACK 2" badge OCR'ing as
# "TRACK 20" or "TRACK 23") -- since the real track number is always exactly
# one digit, taking the first digit *character* (ignoring whatever follows)
# is robust to this; taking the first run of digits is not.
TRACK_DIGIT_RE = re.compile(r"\d")


def load_config() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def normalize_station_name(name: str) -> str:
    name = name.lower().replace("-", " ").replace(".", "")
    return re.sub(r"\s+", " ", name).strip()


def stop_lookup(config: dict) -> dict[str, str]:
    """normalized printed name -> stop_id"""
    lookup = {}
    for stop in config["stops"]:
        for printed in stop["printed_names"]:
            lookup[normalize_station_name(printed)] = stop["stop_id"]
    return lookup


def classify_day_type(header_text: str) -> str:
    if any(w in header_text for w in ("WEEKEND", "SATURDAY", "SUNDAY")):
        return "weekend"
    return "weekday"


def _line_groups(page, merge_tol=2.0) -> list[tuple[float, float, str]]:
    """Every line of text on the page as (top, x0, text), merging words
    whose tops are within merge_tol (handles sub-pixel baseline jitter)."""
    words = page.extract_words(x_tolerance=1)
    buckets: dict[float, list] = defaultdict(list)
    for w in words:
        buckets[round(w["top"], 1)].append(w)
    tops = sorted(buckets)
    merged: list[list] = []
    for t in tops:
        if merged and t - merged[-1][0] <= merge_tol:
            merged[-1][1].extend(buckets[t])
        else:
            merged.append([t, list(buckets[t])])
    lines = []
    for t, ws in merged:
        ws.sort(key=lambda w: w["x0"])
        lines.append((t, ws[0]["x0"], " ".join(w["text"] for w in ws)))
    return lines


def page_blocks(page) -> list[dict]:
    """Find every day-type block on a page. Each block is
    {day_type, header_text, header_top, tables: {section_name: (y0, y1)}}.
    Usually one block per page (sometimes zero, for a cover/legend-only
    page)."""
    lines = _line_groups(page)
    day_type_lines = [
        (top, text) for top, x0, text in lines if text.startswith(DAY_TYPE_PREFIXES)
    ]
    section_lines = sorted(
        ((top, text) for top, x0, text in lines if text in SECTION_HEADERS)
    )

    blocks = []
    for i, (dt_top, dt_text) in enumerate(day_type_lines):
        next_dt_top = day_type_lines[i + 1][0] if i + 1 < len(day_type_lines) else page.height
        my_sections = [(t, name) for t, name in section_lines if dt_top < t < next_dt_top]
        my_sections.sort()
        tables = {}
        for j, (top, name) in enumerate(my_sections):
            end = my_sections[j + 1][0] if j + 1 < len(my_sections) else next_dt_top
            tables[name] = (top, end)
        blocks.append(
            {
                "day_type": classify_day_type(dt_text),
                "header_text": dt_text,
                "header_top": dt_top,
                "tables": tables,
            }
        )
    return blocks


def find_block(pdf, day_type: str) -> tuple[object, dict]:
    """The (page, block) for the first block on any page matching day_type.
    Raises if none or more than one page matches -- ambiguous input should
    fail loudly rather than silently pick one."""
    matches = []
    for page in pdf.pages:
        for block in page_blocks(page):
            if block["day_type"] == day_type:
                matches.append((page, block))
    if not matches:
        raise ValueError(f"no {day_type!r} block found in {pdf.stream.name}")
    if len(matches) > 1:
        tops = [b["header_text"] for _, b in matches]
        raise ValueError(f"multiple {day_type!r} blocks found in {pdf.stream.name}: {tops}")
    return matches[0]


def classify_columns(grid: pdf_grid.Grid) -> dict[int, tuple[str, str]]:
    """column index -> (service_type, train_number), SLE/SLE THRU only."""
    kept = {}
    for c in range(1, grid.num_columns):
        text = grid.cell(0, c)
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        m = COLUMN_HEADER_RE.match(compact)
        if not m:
            continue
        service_type_raw, train_number = m.group(1), m.group(2)
        service_type = "SLE THRU" if service_type_raw == "SLETHRU" else service_type_raw
        if service_type not in ("SLE", "SLE THRU"):
            continue
        kept[c] = (service_type, train_number)
    return kept


# Padding and page-segmentation mode to try, in order -- tesseract on these
# small crops is occasionally flaky (the same crop can OCR to empty text on
# one call and succeed on a retry), so try a few reasonable variations
# before giving up rather than failing on the first miss.
OCR_ATTEMPTS = [(pad, psm) for pad in (3, 2, 5, 4, 6) for psm in (7, 6)]


def ocr_track_badge(page, img: dict, resolution=400) -> int:
    attempts_text = []
    for pad, psm in OCR_ATTEMPTS:
        bbox = (
            max(0, img["x0"] - pad),
            max(0, img["top"] - pad),
            min(page.width, img["x1"] + pad),
            min(page.height, img["bottom"] + pad),
        )
        crop = page.crop(bbox)
        pil: Image.Image = crop.to_image(resolution=resolution).original
        pil = pil.resize((pil.width * 4, pil.height * 4))
        text = pytesseract.image_to_string(pil, config=f"--psm {psm}")
        attempts_text.append(text)
        m = TRACK_DIGIT_RE.search(text)
        if not m:
            continue
        value = int(m.group())
        if 1 <= value <= 9:
            return value
    raise ValueError(
        f"could not OCR a plausible track number from badge at "
        f"({img['x0']:.1f},{img['top']:.1f},{img['x1']:.1f},{img['bottom']:.1f}) "
        f"after {len(OCR_ATTEMPTS)} attempts: {attempts_text!r}"
    )


def row_track_badges(page, grid: pdf_grid.Grid, row: int) -> list[tuple[int, int]]:
    """[(column_index, track_number), ...] ascending by column, for every
    track badge image whose vertical position falls in this row's band."""
    y0, y1 = grid.rows[row], grid.rows[row + 1]
    badges = []
    for img in page.images:
        if y0 <= img["top"] < y1:
            col = grid.column_of(img["x0"])
            badges.append((col, ocr_track_badge(page, img)))
    badges.sort()
    return badges


def track_for_columns(badges: list[tuple[int, int]], columns: list[int]) -> dict[int, int | None]:
    """Propagate each badge's value forward (left to right) to every real
    column at or after it, until the next badge supersedes it -- see
    SPEC.md section 2.3/6 for why this is how SLE's badge placement works."""
    result = {}
    current = None
    bi = 0
    for col in sorted(columns):
        while bi < len(badges) and badges[bi][0] <= col:
            current = badges[bi][1]
            bi += 1
        result[col] = current
    return result


def extract_table(page, y0: float, y1: float, stops: dict[str, str], excluded: set[str]) -> dict:
    """One table (TO NEW HAVEN / TO NEW LONDON / a CONNECTIONS table) ->
    {train_number: {"service_type": ..., "stops": [(stop_id, printed_name,
    time_text, track), ...]}}, in row order (top to bottom, as printed)."""
    grid = pdf_grid.Grid.detect(page, 0, page.width, y0, y1)
    columns = classify_columns(grid)

    result: dict[str, dict] = {}
    for c, (service_type, train_number) in columns.items():
        result[train_number] = {"service_type": service_type, "stops": []}

    for r in range(1, grid.num_rows):
        label = grid.cell(r, 0)
        if not label:
            continue
        key = normalize_station_name(label)
        stop_id = stops.get(key)
        if stop_id is None:
            if label in excluded or key in {normalize_station_name(x) for x in excluded}:
                continue
            raise KeyError(
                f"unmapped station label {label!r} (normalized {key!r}) -- "
                f"add it to config.toml [[stops]] or excluded_connection_stations"
            )
        badges = row_track_badges(page, grid, r)
        tracks = track_for_columns(badges, list(columns))
        for c, (service_type, train_number) in columns.items():
            cell = grid.cell(r, c)
            if not cell:
                continue
            result[train_number]["stops"].append(
                (stop_id, label, cell, tracks.get(c))
            )
    return result


def extract_block(page, block: dict, stops: dict[str, str], excluded: set[str]) -> dict:
    tables = {}
    for name, (y0, y1) in block["tables"].items():
        tables[name] = extract_table(page, y0, y1, stops, excluded)
    return tables


def parse_time(cell: str) -> tuple[int, int]:
    m = TIME_RE.match(cell.strip())
    if not m:
        raise ValueError(f"unparseable time cell {cell!r}")
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm == "P" and hour != 12:
        hour += 12
    if ampm == "A" and hour == 12:
        hour = 0
    return hour, minute


def build_trips(tables: dict, direction_id: int, main_table: str, thru_table: str | None) -> list[dict]:
    """Stitch one direction's trips: the main table (TO NEW HAVEN/TO NEW
    LONDON) plus, for "SLE THRU" trains, the matching CONNECTIONS table's
    SLE THRU column -- merged at the shared New Haven Union Station stop
    (one table gives its arrival, the other its departure)."""
    trips = []
    main = tables[main_table]
    thru = tables.get(thru_table, {}) if thru_table else {}

    for train_number, data in main.items():
        stops = list(data["stops"])
        if data["service_type"] == "SLE THRU":
            thru_data = thru.get(train_number)
            if thru_data is None:
                raise KeyError(
                    f"SLE THRU train {train_number} in {main_table!r} has no matching "
                    f"column in {thru_table!r}"
                )
            thru_stops = thru_data["stops"]
            if direction_id == 1:
                # main table ends at New Haven (arrival); thru table starts
                # at New Haven (departure) and continues to Stamford.
                stops = stops[:-1] + _merge_pivot(stops[-1], thru_stops[0]) + thru_stops[1:]
            else:
                # thru table starts at Stamford and ends at New Haven
                # (arrival); main table starts at New Haven (departure).
                stops = thru_stops[:-1] + _merge_pivot(thru_stops[-1], stops[0]) + stops[1:]
        trips.append(
            {
                "trip_short_name": train_number,
                "direction_id": direction_id,
                "stops": stops,
            }
        )
    return trips


def _merge_pivot(arrival_stop: tuple, departure_stop: tuple) -> list[tuple]:
    """Both are (stop_id, printed_name, time_text, track) for the SAME
    physical stop (New Haven Union Station), one an arrival, one a
    departure. Merge into a single 5-tuple (stop_id, printed_name,
    arrival_time_text, departure_time_text, track)."""
    stop_id_a, name_a, time_a, track_a = arrival_stop
    stop_id_d, name_d, time_d, track_d = departure_stop
    assert stop_id_a == stop_id_d, f"pivot stop mismatch: {stop_id_a} vs {stop_id_d}"
    return [(stop_id_a, name_a, time_a, time_d, track_a or track_d)]


def normalize_stops(stops: list[tuple]) -> list[tuple]:
    """Every entry to the 5-tuple (stop_id, name, arrival_text, departure_text,
    track) shape -- a non-pivot stop has the same text for both."""
    out = []
    for s in stops:
        if len(s) == 5:
            out.append(s)
        else:
            stop_id, name, time_text, track = s
            out.append((stop_id, name, time_text, time_text, track))
    return out


def extract_revision(pdf_path: Path, day_type: str, config: dict) -> list[dict]:
    stops = stop_lookup(config)
    excluded = set(config["extraction"]["excluded_connection_stations"])
    with pdfplumber.open(pdf_path) as pdf:
        page, block = find_block(pdf, day_type)
        tables = extract_block(page, block, stops, excluded)

    all_trips = []
    all_trips += build_trips(tables, 1, "TO NEW HAVEN", "CONNECTIONS TO NEW YORK (DEPARTING)")
    all_trips += build_trips(tables, 0, "TO NEW LONDON", "CONNECTIONS FROM NEW YORK (ARRIVING)")
    for trip in all_trips:
        trip["stops"] = normalize_stops(trip["stops"])
    return all_trips


def trips_to_rows(trips: list[dict]) -> list[dict]:
    rows = []
    for trip in trips:
        for seq, (stop_id, name, arr_text, dep_text, track) in enumerate(trip["stops"], start=1):
            rows.append(
                {
                    "trip_short_name": trip["trip_short_name"],
                    "direction_id": trip["direction_id"],
                    "stop_sequence": seq,
                    "stop_id": stop_id,
                    "printed_name": name,
                    "arrival_text": arr_text,
                    "departure_text": dep_text,
                    "track": track if track is not None else "",
                }
            )
    return rows


FIELDNAMES = [
    "trip_short_name",
    "direction_id",
    "stop_sequence",
    "stop_id",
    "printed_name",
    "arrival_text",
    "departure_text",
    "track",
]


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(EXTRACTED_DIR))
    parser.add_argument("--revision", help="only extract this revision name")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()

    revisions = config["revision"]
    if args.revision:
        revisions = [r for r in revisions if r["name"] == args.revision]
        if not revisions:
            print(f"no revision named {args.revision!r} in config.toml", file=sys.stderr)
            sys.exit(1)

    for rev in revisions:
        pdf_path = SOURCES_DIR / rev["pdf"]
        print(f"extracting {rev['name']} ({rev['day_type']}) from {pdf_path.name}...")
        try:
            trips = extract_revision(pdf_path, rev["day_type"], config)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        rows = trips_to_rows(trips)
        out_path = out_dir / f"{rev['name']}.draft.csv"
        write_csv(out_path, rows)
        print(f"  wrote {len(rows)} stop_times across {len(trips)} trips -> {out_path}")


if __name__ == "__main__":
    main()
