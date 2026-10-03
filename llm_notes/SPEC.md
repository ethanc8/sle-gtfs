# CTrail Shore Line East GTFS — build plan

Status: **planning, not yet implemented**. This is the living plan — update it
as decisions change instead of re-litigating them in chat. Modeled directly on
the sibling project at
`/home/ethan/Projects/Transit/NorthAmerica/Maryland/marc-camden-brunswick-gtfs`
(see its `PLAN.md`/`README.md`/`UPDATING.md`/`PDF-TO-GTFS-LESSONS.md`), whose
architecture we're reusing wholesale where it fits and adapting where SLE's
timetables differ (notably: OCR'd track badges, a single route, and a
config-file-driven revision list per requirement (b) below).

## 0. Decisions (confirmed with the project owner)

- **Route**: one `route_id` for the whole corridor.
  `route_short_name=SLE`, `route_long_name=Shore Line East`,
  `route_color=EA0D2A`, `route_text_color=FFFFFF`.
- **Track numbers**: a non-standard `track` column on `stop_times.txt`,
  mirroring Metro-North's own GTFS convention (§6 below), populated only
  where OCR recovers a badge for that (station, trip) cell.
- **direction_id**: adopt MNR's convention feed-wide — `1` = toward
  NYC/Stamford ("TO NEW HAVEN" table, and the thru-trains' New
  Haven→Stamford leg), `0` = away from NYC ("TO NEW LONDON" table).
- **transfers.txt**: not produced.
- **shapes.txt**: not produced — the consumer will run `pfaedle-rs`
  downstream to derive shapes from real track geometry. Out of scope here.
- **MNR stop_id compatibility is no longer a design constraint.** Since
  Metro-North's own GTFS doesn't contain the SLE thru-trains at all (§2.2),
  there's no real interoperability need being served by borrowing MNR's
  `stop_id`s, and the owner confirmed we should just mint our own IDs
  wherever convenient. This simplifies §3/§4 considerably — see the revised
  version below. (We still use MNR's `stops.txt` as a coordinate *source* for
  stations Amtrak doesn't have — West Haven, Milford, Stratford — since
  that's just the best available data, not an ID-compatibility move.)
- **Calendar end date**: the Oct-5-effective ("until further notice") service
  period runs through **2026-11-01**. No stance taken on Thanksgiving/
  Christmas service — out of scope until a holiday schedule PDF exists.
- **The 10/10–11 outage PDF is a no-op for this build** (§2.4) — confirmed.
  But see the next point: the *pipeline* still needs to handle a real future
  outage.
- **Future outage PDFs must be ingestable.** The owner expects real
  SLE-specific outage schedules (not just "MNR connections degraded") in the
  future. The architecture (§7, §9) is designed so that adding one is a
  config + extracted-CSV change, not a rewrite — see §9's "exception
  revision" design.
- **Output**: gitignored `build/` directory containing both the unzipped feed
  (`build/gtfs/*.txt`) and the zipped artifact (`build/sle-gtfs.zip`).
- **Maintainability (requirement a/b)**: anything that changes with a new
  timetable revision — effective dates, which PDF/tables feed which service
  period, the station list, route branding — lives in a human-editable
  **TOML config file**, not scattered through Python. Only the PDF-layout
  parsing logic (pixel regions, column detection) is code, because the owner
  explicitly carved that out as something a genuine template change would
  require code for anyway. See §9.

## 1. Inputs on disk

```
sources/
  SLE-3_29-MNR-Schedule-Change.pdf             weekday+weekend timetable, eff. 2026-03-29
  SLE-October-5th-schedule_edit.pdf            weekday+weekend timetable, eff. 2026-10-05
  SLE-10_10-Portal-North-Bridge-MNR-WALK-Bridge-Outage-1.pdf   weekend-only, 2026-10-10 & 10-11
  amtrak-gtfs-fixed/   Amtrak GTFS (static) — station coordinates (primary source)
  gtfsmnr/             Metro-North GTFS (static) — coordinate fallback for the 3 stations
                        Amtrak doesn't serve (West Haven, Milford, Stratford), and the
                        design precedent for the `track` stop_times extension column
```

I rendered all three PDFs to PNG and inspected both the extracted text and the
embedded images before writing this plan; findings below drive the design.

## 2. Findings from the source PDFs

### 2.1 Table structure
Each timetable PDF has, per service day-type (Mon–Fri / Weekend-Holiday),
these tables:
- **TO NEW HAVEN** — New London → New Haven (`direction_id=1`). Last row is
  New Haven Union Station.
- **CONNECTIONS TO NEW YORK (DEPARTING)** / **CONNECTIONS FROM NEW YORK
  (ARRIVING)** — columns headed `MNR` (Metro-North's own trains, dropped
  entirely per requirement #8) **and, on weekdays only, one `SLE THRU`
  column**, which is the continuation of an SLE thru-trip from New Haven
  through West Haven/Milford/Stratford/Bridgeport to Stamford (or the
  reverse). **This column must still be extracted** — it's the only place in
  the PDF the West Haven–Stamford leg of a thru-trip appears; it is not an
  MNR train despite sharing a table with MNR columns.
- **TO NEW LONDON** — New Haven → New London (`direction_id=0`).

Row/column text (station names, trip numbers, clock times, already including
an explicit `A`/`P` suffix — no AM/PM-ambiguity problem like the MARC
project had) extracts cleanly with pdfplumber.

### 2.2 "SLE THRU" trains
Trip 1633 (westbound) and 1638 (eastbound) — numbers may differ per revision,
see config — are single, continuous SLE-operated trains that run over
Metro-North's New Haven Line trackage between New Haven and Stamford,
stopping at West Haven, Milford, Stratford, Bridgeport, and Stamford (**not**
South Norwalk or Greenwich — confirmed from blank cells in the "CONNECTIONS"
tables). They exist on weekdays only.

**Metro-North's own GTFS does not contain these trains** — grepping
`gtfsmnr/trips.txt`/`routes.txt` for `SLE`/`Shore` finds nothing; MNR's feed
only models its own six lines. This is why the "share a trip_id with MNR"
idea from the original ask doesn't have a literal target, and why the owner
relaxed the ID-compatibility requirement (§0).

### 2.3 Track numbers are per-(station, trip), not per-station
The `TRACK n` badges are raster images (confirmed via `pdfimages -list`) and
must be OCR'd. They are **not** a fixed attribute of a station: on the considered
pages, the same station (e.g. Branford) shows `TRACK 1` next to some trip
columns and `TRACK 2` next to others, in the same row — consistent with how a
mostly single-tracked branch actually dispatches trains (which siding/track a
given run uses can vary trip by trip). Only Branford, Guilford, Clinton, and
Westbrook ever show a badge in the PDFs inspected so far; Madison, Old
Saybrook, New Haven, and New London never do. This is captured as a
`stop_times.txt`-level field (§6), not a stop-level one.

### 2.4 The 10/10–11 "outage" PDF changes nothing about SLE's own trips
Confirmed by diffing against the Weekend/Holiday tables in the 10/5 PDF: SLE's
trip numbers and every one of their times (3613…3699, 3690…3652) are
byte-for-byte identical. Only `MNR` connection columns differ (several are
missing, replaced with *"Due to Metro-North connection gaps..."*). Since MNR's
own trains are already excluded from this feed, this PDF requires **no
calendar or trip changes** — 2026-10-10/11 already run under the standing
Oct-5+ weekend calendar. Treated as a no-op (§0).

This is exactly the shape of PDF a *real* future outage notice will probably
also take (single page, weekend-or-weekday-only, same InDesign template) —
except next time the SLE columns themselves may actually change (fewer
trains, altered stops, a bus bridge). §9 designs for that case explicitly so
it's a config change, not new code, when it happens — the parsing code
doesn't currently distinguish "no-op" from "real exception"; that's a data
question answered by what's in `extracted/`, not a code path.

### 2.5 Schedules do change between the two main PDFs
3/29 and 10/5's weekday "TO NEW HAVEN" tables are not identical — 10/5 adds
trips 1683 and 1693. These are genuinely different service periods, each
needing its own GTFS trips and service_id, not a relabeled effective date.

## 3. Station list and coordinates

Every SLE timetable station, with coordinate source (requirement #3: use
Amtrak; MNR only where Amtrak has no entry at all):

| Station | Amtrak code | Coordinate source |
|---|---|---|
| New London | NLC | Amtrak |
| Old Saybrook | OSB | Amtrak |
| Westbrook | WSB | Amtrak |
| Clinton | CLN | Amtrak |
| Madison | MDS | Amtrak |
| Guilford | GUI | Amtrak |
| Branford | BNF | Amtrak |
| New Haven – State Street | STS | Amtrak |
| New Haven – Union Station | NHV | Amtrak |
| West Haven | — (not an Amtrak stop) | MNR `stops.txt` (id 190) |
| Milford | — (not an Amtrak stop) | MNR `stops.txt` (id 145) |
| Stratford | — (not an Amtrak stop) | MNR `stops.txt` (id 143) |
| Bridgeport | BRP | Amtrak |
| Stamford | STM | Amtrak |

(South Norwalk and Greenwich are MNR stations the thru-trains never stop at —
not included.)

All thirteen get freshly-minted `stop_id`s in our own namespace (format
`SLE-<code>`, reusing the Amtrak 3-letter code where one exists, and a
hand-picked 3-letter code for West Haven/Milford/Stratford/New Haven-State
St, e.g. `SLE-WHV`, `SLE-MLF`, `SLE-STR`, `SLE-NHS`). This list — printed
name → stop_id → coordinate source — is exactly the kind of thing that could
grow (a new infill station) but rarely will; it lives in the TOML config
(§9), not hardcoded Python, so a human can add a row without touching code.

## 4. Thru-train modeling

Each thru-trip (e.g. `1633`) is built as **one continuous GTFS trip**
spanning New London → Stamford, stitched together from two extracted tables
per §2.1 (the "TO NEW HAVEN" row up to New Haven, then the "SLE THRU" column
of the CONNECTIONS table for the New Haven→Stamford leg) — concatenated by
the extraction step so `build_gtfs.py` just sees one ordered stop list per
trip, same as any other trip.

### trip_id must encode the revision
A trip number like `1633` is reused across *both* the Mar-29 and Oct-5
periods as "the same conceptual run," but on different calendars with
possibly different stop times — GTFS requires `trip_id` to be unique across
the whole feed, not just within one revision. **Correction from an earlier
draft of this plan**, which proposed bare `SLE-1633`: trip_id must be
`SLE-<revision>-<trip_short_name>` (e.g. `SLE-20260329-1633`,
`SLE-20261005-1633`), while `trip_short_name` itself stays the bare printed
number (`1633`) for human-facing lookups/support calls.

## 5. ID namespaces

With MNR-compatibility dropped (§0), this is now simple:

- `agency_id`: `SLE`.
- `route_id`: `SLE` (single route, §0).
- `stop_id`: `SLE-<3-letter-code>`, see §3.
- `service_id`: `SLE-<day-type>-<revision-date>`, e.g. `SLE-WD-20260329`,
  `SLE-WK-20260329`, `SLE-WD-20261005`, `SLE-WK-20261005`.
- `trip_id`: `SLE-<revision-date>-<trip_short_name>`, see §4.

No collision concerns with MNR or Amtrak remain, since we no longer share any
ID space with them (only coordinate values, which aren't an ID).

## 6. Track number representation

Metro-North's own `stop_times.txt` already carries a non-standard `track`
column (and `note_id`, tied to `notes.txt`):
`trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type,track,note_id`.

Given §2.3 (track varies per station+trip, not just per station), we mirror
this: add a `track` column to our own `stop_times.txt`, populated only where
the timetable shows a badge, blank elsewhere. Rejected the "separate stop per
track under a parent station" alternative because the track isn't a fixed
platform attribute here — splitting stops by track would require moving a
trip's `stop_id` based on time of day, for only 4 stations, for no real
benefit (confirmed with owner, §0).

## 7. OCR / extraction pipeline

Based directly on the MARC project's proven approach (its
`PDF-TO-GTFS-LESSONS.md` §1.1, and `gtfs_builder/pdf_grid.py`, which we plan
to **copy in close to as-is** — it's fully agency-agnostic):

1. **Column boundaries**: vertical rule lines in the table's pixel region
   (`page.lines`), deduped. **Row boundaries**: cluster word y-positions
   ("top") by proximity (`pdf_grid.text_rows`) rather than trusting
   pdfplumber's own table-grid guesser — SLE's tables use shading, not
   horizontal rules, same failure mode the MARC project hit.
2. **Identify columns by header content** (a trip-number column header is an
   integer; the `SLE THRU`/`SLE`/`MNR`/`AMTK` row above it says which to
   keep) rather than hardcoded pixel ranges — keeps only `SLE`/`SLE THRU`
   columns, drops `MNR`/`AMTK` ones automatically, and fails loudly (wrong
   column count) if a PDF's layout shifts.
3. **Track badges** (new relative to the MARC project, which had no OCR
   step): get each embedded image's bounding box via pdfplumber's
   `page.images`, crop with Pillow, OCR with `pytesseract` restricted to a
   digit whitelist (`--psm 7 -c tessedit_char_whitelist=0123456789`, since we
   only need the single digit after "TRACK"). Associate each badge with a
   (station, trip) cell the same way a text cell is: badge vertical-center
   falls in a row's y-range, badge horizontal position falls in or
   immediately left of a column's x-range.
4. **Draft-then-promote workflow**, copied from the MARC project
   (`PDF-TO-GTFS-LESSONS.md` §1.6): extraction writes `extracted/*.draft.csv`
   and never touches the promoted `extracted/*.csv` directly. A human (or an
   LLM agent, per requirement (a)) checks every drafted cell — including the
   OCR'd track column, which is the riskiest part of this pipeline — against
   a rendered PNG (`make render-pdfs`) before promoting. This is the
   project's actual correctness guarantee, not the extraction code itself.

## 8. Toolchain

Python via pixi (mirroring the MARC project's `pixi.toml` almost exactly),
plus `pillow`/`pytesseract`/`tesseract` for the OCR step it didn't need:
`python`, `pdfplumber`, `poppler`, `pillow`, `pytesseract`, `tesseract`
(conda-forge ships the binary), `pytest`. A Rust `validator/` crate via
cargo, using `gtfs-structures` — planning to **copy the MARC project's
`validator/src/main.rs` essentially unchanged**, since it's fully
GTFS-generic (referential integrity + monotonic stop times), per its own
`PDF-TO-GTFS-LESSONS.md` §2 recommendation.

## 9. Config file (requirements a, b) and repo layout

A single TOML file, `gtfs_builder/config.toml`, holds everything that's
*data* about this feed rather than *PDF-parsing logic*:

```toml
[agency]
agency_id = "SLE"
agency_name = "CTrail Shore Line East"
agency_url = "https://shorelineeast.com"
agency_phone = "1-877-287-4337"
agency_timezone = "America/New_York"
agency_lang = "en"

[route]
route_id = "SLE"
route_short_name = "SLE"
route_long_name = "Shore Line East"
route_color = "EA0D2A"
route_text_color = "FFFFFF"

[[stops]]
printed_name = "New London"
stop_id = "SLE-NLC"
coordinate_source = "amtrak"
coordinate_source_id = "NLC"
# ... one entry per station in §3's table ...
[[stops]]
printed_name = "West Haven"
stop_id = "SLE-WHV"
coordinate_source = "mnr"
coordinate_source_id = "190"

# One entry per timetable revision. "standing" = ordinary day-of-week
# calendar over a date range; "exception" = explicit dated service,
# calendar_dates.txt exception_type=1, for a one-off outage/holiday PDF.
# This is how requirement #7 (future real outage PDFs) gets supported
# without new code: add a PDF, add one of these per day-type it covers,
# add the matching extracted/*.csv files, rerun `make build`.
[[revision]]
name = "20260329"
pdf = "SLE-3_29-MNR-Schedule-Change.pdf"
type = "standing"
day_type = "weekday"
start_date = "2026-03-29"
end_date = "2026-10-04"

[[revision]]
name = "20260329"
pdf = "SLE-3_29-MNR-Schedule-Change.pdf"
type = "standing"
day_type = "weekend"
start_date = "2026-03-29"
end_date = "2026-10-04"

[[revision]]
name = "20261005"
pdf = "SLE-October-5th-schedule_edit.pdf"
type = "standing"
day_type = "weekday"
start_date = "2026-10-05"
end_date = "2026-11-01"

[[revision]]
name = "20261005"
pdf = "SLE-October-5th-schedule_edit.pdf"
type = "standing"
day_type = "weekend"
start_date = "2026-10-05"
end_date = "2026-11-01"

# The 10/10-11 outage PDF: included for completeness/traceability even
# though extraction shows it's a no-op (§2.4) -- `notes` records why no
# exception rows were generated, so a future reader doesn't have to
# re-derive that finding. A REAL future outage would instead set
# dropped_trips/extra stop-pattern info here once we design that field —
# see TODO below.
[[revision]]
name = "20261010-11-outage"
pdf = "SLE-10_10-Portal-North-Bridge-MNR-WALK-Bridge-Outage-1.pdf"
type = "exception"
day_type = "weekend"
dates = ["2026-10-10", "2026-10-11"]
no_op = true
notes = "SLE trip numbers/times identical to the standing 20261005 weekend calendar; only MNR connections (out of scope) differ. No calendar_dates.txt rows emitted."
```

Pixel bbox regions for table extraction, per the owner's own carve-out in
requirement (b), stay in Python (`pdf_extract.py`'s equivalent of the MARC
project's `TABLES` list) since a genuine template change needs code changes
regardless of config.

### Repo layout (mirrors the MARC project's four-directory split)

```
sources/                     # raw inputs (existing) — never modified
  *.pdf
  amtrak-gtfs-fixed/
  gtfsmnr/

gtfs_builder/                  # Python package (pixi env)
  config.toml                  # §9 — the human/agent-editable revision & station list
  pdf_grid.py                  # column/row grid detection — ~copied from the MARC project
  pdf_extract.py                # per-revision TABLES list (pixel regions) -> extracted/*.draft.csv
  pdf_track_badges.py           # OCR step (new vs. MARC project)
  build_gtfs.py                 # config.toml + extracted/ -> build/gtfs/*.txt + zip
  render_pdfs.py                 # PDF pages -> build/pdf-preview/*.png
  test_*.py

extracted/                    # timetable data pulled from the PDFs, hand-verified
                               # against build/pdf-preview/ before being trusted
  20260329_weekday_to_new_haven.csv
  20260329_weekday_to_new_london.csv
  20260329_weekday_thru.csv
  20260329_weekend_to_new_haven.csv
  ... (one set per revision/day_type, per §9's revision list)

validator/                     # Rust crate (cargo) — ~copied from the MARC project
  src/main.rs

build/                         # generated, gitignored
  pdf-preview/*.png
  gtfs/*.txt
  sle-gtfs.zip

llm_notes/SPEC.md               # this file
pixi.toml / pixi.lock
Cargo.toml (validator/)
Makefile
```

Makefile targets, same shape as the MARC project: `render-pdfs`,
`extract-draft`, `build`, `validate`, `test`, `all`, `clean`.

## 10. Validation plan

- Structural: the Rust validator (referential integrity + non-decreasing
  stop times per trip).
- Manual: every promoted `extracted/*.csv` cell checked against
  `build/pdf-preview/*.png` by eye before being trusted (§7.4) — this is the
  real check for OCR/grid-association correctness, not an automated one.
- Cross-stop pivot check: New Haven Union Station is the one stop every
  table (TO NEW HAVEN, TO NEW LONDON, and the thru leg) agrees on — worth a
  specific diff/assertion that a trip's New Haven time, pulled from whichever
  table(s) mention it, agrees everywhere it appears.

## 11. Remaining open items

1. `wheelchair_boarding`: not clearly stated per-station in the SLE PDFs.
   Propose leaving `0` (unknown) for the 7 SLE-only stations, and optionally
   copying MNR's value for the 6 name-overlapping stations (West
   Haven/Milford/Stratford/Bridgeport/New Haven/Stamford) as a courtesy,
   purely as data — no ID sharing implied. OK to do that, or leave all
   unknown?
2. `bikes_allowed`: the PDFs state *"Your bicycle can now ride on board every
   CTrail Shore Line East train"* with no reservation needed (unlike Amtrak).
   Propose `bikes_allowed=1` on every SLE trip. Confirm?
3. `feed_info.txt`: propose `feed_publisher_name=CTrail Shore Line East`,
   `feed_publisher_url=https://shorelineeast.com`,
   `feed_start_date=2026-03-29`, `feed_end_date=2026-11-01`,
   `feed_version` = today's build date. Confirm, or specify differently?
4. The config schema's `exception` revision type (§9) currently only has a
   `no_op` escape hatch, since that's all we need for the one exception PDF
   in hand. Before a *real* future outage PDF arrives, this needs fields for
   what actually changes (cancelled trips, added trips, altered stop
   patterns) — I'll design that properly against the actual PDF when one
   shows up rather than guessing at a schema now; flagging so it's not
   forgotten.
