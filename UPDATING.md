# Updating this feed for a new schedule PDF

CTrail periodically republishes the Shore Line East timetable (a service
change, a holiday/outage notice, etc.). This is the checklist for bringing
this repo up to date when that happens. Read `llm_notes/SPEC.md` first if you
haven't — this assumes you know the pipeline it describes (`sources/` →
`extracted/` → `gtfs_builder/` → `build/`).

Work through the sections in order; later steps assume earlier ones are done.

## 1. Add the new PDF

Drop it into `sources/` (keep the old ones too — don't delete history you
might need to diff against later, the way `sources/older_examples/` is kept
around as extraction regression tests).

If CTrail changed the InDesign template (not just new numbers/dates in the
same layout), skip to **section 5 (re-tuning extraction)** before continuing.
Otherwise the existing `gtfs_builder/pdf_grid.py`/`pdf_extract.py` should
handle it unchanged — the extraction is driven by searching for known
section-header text and classifying columns by their header content
(`SLE`/`SLE THRU` vs `AMTK`/`MNR`), not by hardcoded pixel coordinates, so a
new PDF using the same template needs zero code changes.

## 2. Add a `[[revision]]` entry (or several) to `config.toml`

See `gtfs_builder/config.toml`'s own comments for the two revision shapes:

- **`type = "standing"`**: an ordinary day-of-week calendar over a date
  range (what the 2026-03-29 and 2026-10-05 schedules are). Add one entry
  per day-type (`weekday`/`weekend`) the PDF covers, each with
  `start_date`/`end_date`.
- **`type = "exception"`**: a calendar_dates.txt-only service active on
  specific `dates`. **Every exception revision is a fully independent
  calendar service** — its trips get their own trip_ids/stop_times, never
  shared with a standing revision's trips even if the printed schedule is
  identical (see the `20261010-11-outage` entry for a worked example), and
  `build_gtfs.py` automatically suppresses whichever standing service would
  otherwise run on those dates. You don't need to figure out which standing
  service that is — `suppressed_services_for()` does that from the calendar
  semantics directly.

  Set **`no_op = true`** on an exception revision once extraction confirms
  it doesn't actually change SLE's own schedule (e.g. an outage notice that
  only affects MNR connections) — `build_gtfs.py` then skips it entirely
  (no trips, no calendar_dates rows), rather than emitting a redundant
  duplicate service. Extract it and keep the `extracted/<name>.csv` file
  checked in either way, as the record of *why* it's a no-op — see the
  `20261010-11-outage` entry's `notes` for the expected level of detail.
  Leave `no_op` unset (or `false`) for a real exception that does change
  something.

If a new station appears, add a `[[stops]]` entry. `stop_name` is for
display/headsigns, `printed_names` lists every spelling the PDFs use. For
`stop_id`/`stop_code`: if the station is served by Metro-North, use MNR's
own `stop_code` from `sources/gtfsmnr/stops.txt` (as `SLE-<code>` /
`<code>` respectively) — not Amtrak's or an invented one — so a future
MNR-realtime-to-GTFS-RT converter can match stops directly; otherwise use
Amtrak's stop_id the way the existing SLE-only stations do. Coordinates:
see the comment at the top of `config.toml` for sourcing (Amtrak preferred,
MNR fallback for the 3 stations Amtrak doesn't serve). **Do not** have the
pipeline read `sources/amtrak-gtfs-fixed/`/`sources/gtfsmnr/` at build time
— copy the numbers in by hand once, the way every existing entry did.
Changing a `stop_id` after the fact requires re-running `make extract-draft`
and re-promoting every `extracted/*.csv` (the station lookup is baked in at
extraction time).

## 3. Extract the new timetable

```sh
make render-pdfs     # PNGs in build/pdf-preview/, for eyeballing cells
make extract-draft   # writes extracted/<revision-name>.draft.csv
```

**Manually verify every drafted cell — including the OCR'd `track`
column, the riskiest part of this pipeline — against the rendered PNG
before promoting:**

```sh
cp extracted/<revision-name>.draft.csv extracted/<revision-name>.csv
```

This project's whole premise is that the timetable is exact — don't skip
this because the extraction "usually works." Pay particular attention to:

- Any trip that changed from a previous revision (new trip numbers, added/
  removed stops) — these are the most likely source of a real schedule
  change you need to get right, not an extraction bug.
- Track badges — OCR occasionally needs a retry internally (see
  `pdf_extract.OCR_ATTEMPTS`) and has a plausibility check (1-9), but neither
  catches a confident misread of e.g. "3" as "8".
- If `pdf_extract.py` raises `KeyError: unmapped station label ...`: either
  a genuinely new station (see step 2) or a station name spelled
  differently than any existing `printed_names` entry — add the new
  spelling to the right stop's list, or to `[extraction]
  excluded_connection_stations` if it's a Metro-North-only station that
  shows up as a row in a CONNECTIONS table (like South Norwalk/Greenwich/
  Harlem-125th St/Grand Central Terminal already are).

## 4. A real SLE-specific outage/exception

If a future outage PDF actually changes SLE's own trips (not just MNR
connections, like 2026-10-10/11's did) — fewer trains, altered stops, a bus
bridge — no special handling is needed beyond what's already in section 2/3:
add it as a `type = "exception"` revision, extract it like any other PDF,
and the stitched trips will reflect whatever the PDF actually shows. The
pipeline doesn't have (and doesn't need) a separate code path for "this
exception changes real service" vs. "this exception is informational" —
that distinction lives entirely in what's in `extracted/<name>.csv`, not in
`gtfs_builder/*.py`.

## 5. Re-tuning PDF extraction (only if the layout changed)

`pdf_extract.py`'s docstring and `pdf_grid.py` explain the technique: every
table is a true ruled grid (both vertical AND horizontal rule lines are
present — confirmed by inspecting `page.lines`), so column/row boundaries
come directly from that vector geometry, not from clustering word
positions. Section (`TO NEW HAVEN`/`CONNECTIONS...`/`TO NEW LONDON`) and
day-type (`MONDAY TO FRIDAY`/`WEEKEND/HOLIDAY`/etc.) boundaries are found by
searching for that literal text on the page (`pdf_extract.page_blocks`), so
a PDF using the same template needs no coordinate changes at all.

If CTrail changes the template, to find the new structure:

```python
import pdfplumber
pdf = pdfplumber.open("sources/<the new pdf>")
page = pdf.pages[0]
from gtfs_builder.pdf_extract import _line_groups
for top, x0, text in _line_groups(page):
    print(top, x0, text)
```

to find the new section/day-type header text and positions, then

```python
from gtfs_builder import pdf_grid
g = pdf_grid.Grid.detect(page, 0, page.width, Y0, Y1)
print(g.num_rows, g.num_columns)
for c in range(g.num_columns):
    print(c, repr(g.cell(0, c)))
```

to inspect a table's header row once you've found its `(Y0, Y1)` bounds, the
way `llm_notes/SPEC.md` section 7 was originally worked out.

## 6. Build, validate, and spot-check

```sh
make all
```

If `build_gtfs.py` raises `FileNotFoundError`: you haven't promoted a
revision's draft CSV yet (step 3).

Once it builds clean, spot-check `build/gtfs/stop_times.txt` for a handful
of trips against the rendered PDFs — the automated checks (unit tests, the
Rust validator) catch structural problems, not "the extraction quietly
picked up the wrong number."

## 7. Update the docs

- `llm_notes/SPEC.md` if anything about how the pipeline or a schedule
  nuance works turned out to differ from what's documented there.
- `README.md`'s "Known limitations" if this update resolved or changed one.
- This file, if the update surfaced a new recurring wrinkle worth
  remembering for next time.
