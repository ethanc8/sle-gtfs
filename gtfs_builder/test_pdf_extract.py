"""Tests for the PDF extraction pipeline.

Includes regression tests against every PDF in sources/older_examples/ (not
just the 3 PDFs actually used in config.toml's [[revision]] list) --- per the
project owner, these were added specifically "to test the implementation
against": confirming the column/row-grid detection, the SLE/SLE THRU vs
AMTK/MNR column filtering, and the track-badge OCR all generalize across
independently-issued PDFs (different years, a weekday-pattern holiday
schedule, a weekend-pattern holiday schedule, and a 2-page standing
schedule) rather than being tuned to only the 3 PDFs actually shipped.
"""

from pathlib import Path

import pytest

from gtfs_builder import pdf_extract as X

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "sources"
OLDER_DIR = SOURCES_DIR / "older_examples"


@pytest.fixture(scope="module")
def config():
    return X.load_config()


def test_normalize_station_name():
    assert X.normalize_station_name("New Haven - Union Station") == "new haven union station"
    assert X.normalize_station_name("New Haven Union Station") == "new haven union station"
    assert X.normalize_station_name("Harlem - 125th St.") == "harlem 125th st"
    assert X.normalize_station_name("  Old   Saybrook ") == "old saybrook"


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
    ],
)
def test_extract_shipped_revisions(config, revision_name, day_type, expected_trip_count, expected_stop_count):
    rev = next(r for r in config["revision"] if r["name"] == revision_name)
    pdf_path = SOURCES_DIR / rev["pdf"]
    trips = X.extract_revision(pdf_path, day_type, config)
    assert len(trips) == expected_trip_count
    assert sum(len(t["stops"]) for t in trips) == expected_stop_count


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


def test_outage_pdf_matches_standing_weekend_schedule(config):
    """SPEC.md section 2.4's finding, as a regression test: the 10/10-11
    outage PDF's SLE trips/times are identical to the standing
    20261005-weekend revision (only MNR connection columns, out of scope,
    differ)."""
    outage_rev = next(r for r in config["revision"] if r["name"] == "20261010-11-outage")
    standing_rev = next(r for r in config["revision"] if r["name"] == "20261005-weekend")
    outage_trips = X.extract_revision(SOURCES_DIR / outage_rev["pdf"], "weekend", config)
    standing_trips = X.extract_revision(SOURCES_DIR / standing_rev["pdf"], "weekend", config)

    def normalize(trips):
        return sorted(
            (t["trip_short_name"], t["direction_id"], tuple((s[0], s[2], s[3], s[4]) for s in t["stops"]))
            for t in trips
        )

    assert normalize(outage_trips) == normalize(standing_trips)
