"""Tests for the PDF extraction pipeline.

Includes regression tests against every PDF in sources/older_examples/ (not
just the 3 PDFs actually used in config.toml's [[revision]] list) --- per the
project owner, these were added specifically "to test the implementation
against": confirming the column/row-grid detection, the SLE/SLE THRU vs
AMTK/MNR column filtering, and the track-badge OCR all generalize across
independently-issued PDFs (different years, a weekday-pattern holiday
schedule, a weekend-pattern holiday schedule, and a 2-page standing
schedule) rather than being tuned to only the 3 PDFs actually shipped.

A note on live extraction vs. reading extracted/*.csv in these tests: the
track-badge OCR was found to be non-deterministic run-to-run (the exact same
code against the exact same PDF produced a different digit on different
invocations -- see llm_notes/SPEC.md section 15). Tests that need an exact
fact about a schedule (e.g. "these two PDFs produce identical track
assignments") read the already-promoted, human-verified extracted/*.csv
files instead of re-running OCR live, since that data is deterministic and
is the pipeline's actual source of truth once promoted. Tests here that DO
re-run extraction live are deliberately limited to checks that don't depend
on getting any individual OCR'd digit right (trip/stop *counts*, which only
change if row/column detection itself breaks) -- and to sources/older_examples/,
which has no promoted-CSV equivalent to compare against.
"""

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from gtfs_builder import pdf_extract as X

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "sources"
OLDER_DIR = SOURCES_DIR / "older_examples"
EXTRACTED_DIR = REPO_ROOT / "extracted"


def load_promoted(name: str) -> list[dict]:
    """The already-promoted, human-verified extracted/<name>.csv -- the
    deterministic ground truth for a shipped revision, as opposed to
    re-running (nondeterministic) OCR against the source PDF."""
    with (EXTRACTED_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fingerprint_rows(rows: list[dict]) -> set[tuple]:
    """(trip_short_name, direction_id, stop_sequence, stop_id, arrival,
    departure, track) tuples -- an order-independent fingerprint of a
    promoted CSV's actual content, for exact-match comparison. Time text is
    whitespace-normalized ("7:50A" vs "7:50 A") since different PDF files
    render the same time with different internal spacing -- a cosmetic
    typesetting difference TIME_RE already tolerates, not a real one."""
    return {
        (
            r["trip_short_name"],
            r["direction_id"],
            r["stop_sequence"],
            r["stop_id"],
            r["arrival_text"].replace(" ", ""),
            r["departure_text"].replace(" ", ""),
            r["track"],
        )
        for r in rows
    }


@pytest.fixture(scope="module")
def config():
    return X.load_config()


def test_normalize_station_name():
    assert X.normalize_station_name("New Haven - Union Station") == "new haven union station"
    assert X.normalize_station_name("New Haven Union Station") == "new haven union station"
    assert X.normalize_station_name("Harlem - 125th St.") == "harlem 125th st"
    assert X.normalize_station_name("  Old   Saybrook ") == "old saybrook"
    # A superscript ordinal suffix can land with a space before it after
    # text extraction even once correctly reordered (see pdf_grid.cell_text
    # and SPEC.md section 16) -- "125 th" must normalize the same as "125th".
    assert X.normalize_station_name("Harlem - 125 th St.") == X.normalize_station_name("Harlem - 125th St.")


def test_strip_track_from_label():
    assert X.strip_track_from_label("TRACK 2 Westbrook") == ("Westbrook", 2)
    assert X.strip_track_from_label("Clinton TRACK 2") == ("Clinton", 2)
    assert X.strip_track_from_label("Madison") == ("Madison", None)


def test_row_text_track_badges():
    grid = SimpleNamespace(
        num_columns=5,
        cell=lambda row, col: {1: "6:14 A", 2: "TRACK 2", 3: "11:54 A", 4: "9:58 A"}.get(col, ""),
    )
    # column 1 and 4 are real trip columns (times); column 2 is a blank
    # spacer carrying a mid-row track change, same as the image-badge PDFs.
    badges = X.row_text_track_badges(grid, 0, real_columns={1, 3, 4}, label_track=2)
    assert badges == [(0, 2), (2, 2)]


def test_classify_day_type():
    assert X.classify_day_type("MONDAY TO FRIDAY | LUNES A VIERNES") == "weekday"
    assert X.classify_day_type("WEEKEND/HOLIDAY | FIN DE SEMANA / DIA FESTIVO") == "weekend"
    assert X.classify_day_type("SATURDAY AND SUNDAY | SABADO Y DOMINGO") == "weekend"
    assert X.classify_day_type("MONDAY | LUNES") == "weekday"
    assert X.classify_day_type("THURSDAY | JUEVES") == "weekday"


def test_track_for_columns_propagates_forward():
    # badge at column 0 (the row-label area) sets the initial track; a badge
    # at column 6 updates it for columns >= 6; a badge at column 9 updates it
    # again -- matches the real layout in SPEC.md section 2.3/6.
    badges = [(0, 2), (6, 2), (9, 1)]
    columns = [1, 3, 5, 7, 8, 10, 11]
    result = X.track_for_columns(badges, columns)
    # column 9's badge takes effect starting at column 10 (the next real
    # column at or after it); columns 7 and 8 still see the column-6 badge.
    assert result == {1: 2, 3: 2, 5: 2, 7: 2, 8: 2, 10: 1, 11: 1}


def test_track_for_columns_no_badges():
    assert X.track_for_columns([], [1, 2, 3]) == {1: None, 2: None, 3: None}


def test_merge_pivot():
    arrival = ("SLE-2NH", "New Haven - Union Station", "6:51 A", 5)
    departure = ("SLE-2NH", "New Haven Union Station", "6:55 A", None)
    merged = X._merge_pivot(arrival, departure)
    assert merged == [("SLE-2NH", "New Haven - Union Station", "6:51 A", "6:55 A", 5)]


@pytest.mark.parametrize(
    "revision_name,day_type,expected_trip_count,expected_stop_count",
    [
        ("20260329-weekday", "weekday", 20, 182),
        ("20260329-weekend", "weekend", 16, 135),
        ("20261005-weekday", "weekday", 24, 216),
        ("20261005-weekend", "weekend", 16, 135),
        ("20261116-weekday", "weekday", 24, 214),
        ("20261116-weekend", "weekend", 16, 135),
    ],
)
def test_extract_shipped_revisions(config, revision_name, day_type, expected_trip_count, expected_stop_count):
    rev = next(r for r in config["revision"] if r["name"] == revision_name)
    pdf_path = SOURCES_DIR / rev["pdf"]
    trips = X.extract_revision(pdf_path, day_type, config)
    assert len(trips) == expected_trip_count
    assert sum(len(t["stops"]) for t in trips) == expected_stop_count


def _trip_times(trips):
    """{(trip_short_name, stop_id): (arrival_text, departure_text)} -- for
    comparing two extractions' actual printed times, independent of stop
    order within a trip."""
    return {(t["trip_short_name"], s[0]): (s[2], s[3]) for t in trips for s in t["stops"]}


def test_20261116_weekday_drops_new_london_for_two_trips(config):
    """Two of SLE-1116-Yearlong-View-to-Crescent-Track-Outage_v2.pdf's real
    schedule changes vs. the standing 20261005-weekday revision, confirmed
    by reading the rendered PDF directly (not just diffing extraction
    output) -- see llm_notes/SPEC.md section 16. This PDF's track numbers
    are real text, not OCR'd images, so unlike the image-badge PDFs this
    comparison is fully deterministic and safe to do via live extraction.
    (These two stop-pattern changes are NOT the only difference from the
    standing schedule -- see test_20261116_weekday_times_actually_differ_
    from_standing_weekday below for the rest.)"""
    rev = next(r for r in config["revision"] if r["name"] == "20261116-weekday")
    trips = X.extract_revision(SOURCES_DIR / rev["pdf"], "weekday", config)

    trip_1693 = next(t for t in trips if t["trip_short_name"] == "1693")
    assert trip_1693["stops"][0][0] == "SLE-OSB"  # starts at Old Saybrook, not New London

    trip_1640 = next(t for t in trips if t["trip_short_name"] == "1640")
    assert trip_1640["stops"][-1][0] == "SLE-OSB"  # ends at Old Saybrook, not New London


@pytest.mark.parametrize(
    "standing_name,new_name,day_type",
    [
        ("20261005-weekday", "20261116-weekday", "weekday"),
        ("20261005-weekend", "20261116-weekend", "weekend"),
    ],
)
def test_20261116_times_actually_differ_from_standing(config, standing_name, new_name, day_type):
    """SLE-1116-...-v2.pdf is a near-total retiming on BOTH schedules, not a
    small tweak -- almost every trip's times shifted by a few minutes
    (confirmed by reading the rendered PDF, not just this test). This is a
    regression guard specifically against the mistake that happened twice
    while building this revision: a manual `cut`-column diff that
    accidentally excluded the time columns entirely, making both schedules
    look unchanged except for the stop-pattern changes above -- see
    llm_notes/SPEC.md section 16. A fingerprint over every (trip, stop)
    pair's actual printed times is what catches that mistake; comparing a
    hand-picked column subset (as the original manual diff did) would not."""
    standing_rev = next(r for r in config["revision"] if r["name"] == standing_name)
    new_rev = next(r for r in config["revision"] if r["name"] == new_name)
    standing_trips = X.extract_revision(SOURCES_DIR / standing_rev["pdf"], day_type, config)
    new_trips = X.extract_revision(SOURCES_DIR / new_rev["pdf"], day_type, config)

    standing_times = _trip_times(standing_trips)
    new_times = _trip_times(new_trips)
    # same (trip, stop) pairs overall (the two New-London stop-pattern
    # changes above are trip-specific, not a wholesale reshuffle), but the
    # actual times must differ for this to be a real retiming.
    assert standing_times != new_times


def test_thru_trip_shares_new_haven_pivot_with_distinct_arrival_departure(config):
    rev = next(r for r in config["revision"] if r["name"] == "20261005-weekday")
    trips = X.extract_revision(SOURCES_DIR / rev["pdf"], "weekday", config)
    trip_1633 = next(t for t in trips if t["trip_short_name"] == "1633")
    pivot = next(s for s in trip_1633["stops"] if s[0] == "SLE-2NH")
    _, _, arrival_text, departure_text, _ = pivot
    assert arrival_text == "6:51 A"
    assert departure_text == "6:55 A"
    # the trip must continue past New Haven to Stamford, not stop there
    assert trip_1633["stops"][-1][0] == "SLE-2SM"


@pytest.mark.parametrize(
    "filename,day_type,expected_trip_count,expected_stop_count",
    [
        ("SLE-1_19-MLK-Schedule-MLK-Jr.-Day.pdf", "weekday", 16, 135),
        ("Thanksgiving-Schedule-Wednesday-11_26.pdf", "weekday", 20, 177),
        ("Thanksgiving-Schedule-Thanksgiving-11_27-1.pdf", "weekday", 16, 135),
        ("Thanksgiving-Schedule-Sat-11_29_Sun-11_30.pdf", "weekend", 16, 135),
        ("SLE-Oct.-5-website-Schedule_R2_09262025.pdf", "weekday", 20, 182),
        ("SLE-Oct.-5-website-Schedule_R2_09262025.pdf", "weekend", 16, 135),
    ],
)
def test_extract_older_examples(config, filename, day_type, expected_trip_count, expected_stop_count):
    """Regression test against sources/older_examples/ -- PDFs the project
    owner added specifically to confirm extraction generalizes beyond the 3
    PDFs actually used in config.toml. These are single-day exception-style
    PDFs (MLK, 3 Thanksgiving variants) and an independent prior-year
    standing PDF, not part of the shipped feed's calendar."""
    trips = X.extract_revision(OLDER_DIR / filename, day_type, config)
    assert len(trips) == expected_trip_count
    assert sum(len(t["stops"]) for t in trips) == expected_stop_count


@pytest.mark.parametrize(
    "outage_name,standing_name",
    [
        ("20261010-11-outage", "20261005-weekend"),
        ("20261012-outage", "20261005-weekday"),
    ],
)
def test_noop_outage_matches_standing_schedule_exactly(config, outage_name, standing_name):
    """Both no_op=true outage revisions (see config.toml) are regression-
    tested here, not just asserted: the promoted extracted/*.csv for the
    outage PDF must be identical (times AND track assignments) to the
    corresponding standing revision's -- that identity is *why* each is
    marked no_op. Only MNR connection columns (out of scope, requirement #8)
    differ between the actual PDFs.

    Deliberately compares the PROMOTED csvs, not a fresh live extraction of
    either PDF -- see this module's docstring for why: re-extracting live
    found 20 track "mismatches" against the standing schedule for
    20261012-outage that all turned out to be OCR misreads specific to that
    invocation, not real differences, confirmed by reading the rendered
    badges by eye (llm_notes/SPEC.md section 15). Comparing tracks here, not
    just times, is deliberate -- a pure-time comparison would have missed
    that bug entirely."""
    outage_rev = next(r for r in config["revision"] if r["name"] == outage_name)
    assert outage_rev.get("no_op") is True  # guard against the config drifting silently
    assert fingerprint_rows(load_promoted(outage_name)) == fingerprint_rows(load_promoted(standing_name))
