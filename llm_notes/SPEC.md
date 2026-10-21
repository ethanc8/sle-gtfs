# CTrail Shore Line East GTFS — build plan

Status: **implemented** (`make all` builds and validates clean). See section
12 for what changed between this plan and the actual implementation — read
that section first if you're trying to understand the real pipeline rather
than the design history. This is still the living plan — update it as
decisions change instead of re-litigating them in chat. Modeled directly on
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

## 11. Open items from the planning phase — resolved

1. `wheelchair_boarding`: resolved — the project owner confirmed all SLE
   stations have step-free access, so this is `1` for every stop
   unconditionally. No MNR data borrowed.
2. `bikes_allowed`: resolved — `1` on every trip, confirmed.
3. `feed_info.txt`: resolved — used as proposed, with a static
   `feed_version` (the latest source PDF's effective date, `2026-10-05`)
   rather than a wall-clock build date, for reproducible builds.
4. The exception-revision schema question is now moot: the project owner
   clarified that *every* exception revision — whether or not it actually
   changes SLE's own schedule — gets a fully independent service_id and its
   own trip_ids/stop_times, with the standing calendar suppressed on those
   dates. There's no separate "what changed" schema needed; see section 12.

## 12. What changed during implementation

A few things turned out differently than planned once real PDFs were
inspected closely in code (not just by eye) — recorded here so this document
stays trustworthy as a description of what the pipeline actually does.

- **The tables have real horizontal rule lines.** The original plan (section
  7) assumed, by analogy with the MARC sibling project, that row boundaries
  would need to come from clustering word y-positions, with no horizontal
  vector lines to rely on. This was wrong for SLE's PDFs specifically — the
  project owner caught it: `page.lines` contains a horizontal rule at every
  row boundary, including the header row's. `pdf_grid.py` ended up using
  **both** axes as true ruled-grid detection (`column_boundaries`/
  `row_boundaries`/`Grid`), which is simpler and more precise than the
  word-clustering approach MARC needed — no row_gap tuning at all. Don't
  assume a technique that worked on a different agency's PDFs applies here
  without checking `page.lines` directly first.
- **Track badges are placed in "spacer" columns, not attached to specific
  trip columns**, and their value *propagates forward* to every real column
  until the next badge. Confirmed empirically by cropping and comparing
  badge images at different x-positions within the same row (see the
  session transcript around `ocr_track_badge`/`track_for_columns`) — the
  same physical station can show `TRACK 1` for its morning trains and
  `TRACK 2` for its afternoon trains, with the badge sitting in a blank grid
  column between trip-number groups rather than inside any specific trip's
  own cell. `pdf_extract.track_for_columns` implements the propagation; see
  its test in `test_pdf_extract.py` for a worked example.
- **`pdf_extract.py` ended up as one module**, not split into a separate
  `pdf_track_badges.py` as section 9's repo layout sketched — the track-badge
  OCR is small enough, and tightly enough coupled to the same `Grid`/row
  concept as text-cell extraction, that splitting it added indirection
  without a real benefit.
- **One CONNECTIONS table's header text is letter-spaced** enough that
  pdfplumber reads each glyph as a separate "word" (e.g. `SLE T H RU 16 3 3`
  instead of `SLE THRU 1633`). Column classification strips all whitespace
  before pattern-matching rather than assuming a single space between
  tokens — see `COLUMN_HEADER_RE`'s comment in `pdf_extract.py`.
- **Tesseract is occasionally flaky on these small crops**: the exact same
  crop can OCR to empty text on one call and succeed on a retry (observed,
  not theoretical — see the session transcript). `ocr_track_badge` retries
  across a handful of crop-padding/page-segmentation-mode combinations
  before giving up, rather than trusting the first attempt. Also: the arrow
  glyph (▷) after the digit is sometimes misread as an extra trailing digit
  with no separating space (`TRACK 2` → `"TRACK 20"`) — matching the first
  single digit *character*, not the first run of digits, avoids this.
- **MNR stop_id compatibility was dropped** (confirmed with the owner,
  section 0) once it became clear MNR's feed doesn't contain the thru-trains
  at all — sections 3-5's original MNR-shared-stop_id design was replaced
  with a single flat `SLE-*` namespace for all 14 stations. Coordinates
  still come from Amtrak (primary) or MNR (fallback for West Haven/Milford/
  Stratford, which Amtrak doesn't serve) — but as literal numbers baked into
  `config.toml` once, not a runtime dependency on either feed (per the
  owner: "the converter probably shouldn't read the Amtrak or MNR GTFS
  feeds; anything it needs should be in the config file").
- **Exception revisions are fully independent calendar services**, per
  explicit owner direction: every trip under an exception revision gets its
  own `trip_id`/`stop_times`, *even when byte-identical* to a standing
  revision's trips (the 2026-10-10/11 outage's SLE schedule, confirmed
  identical to the standing weekend pattern — section 2.4 — is still built
  as 16 fully separate trips under `SLE-20261010-11-outage`, not reused from
  `SLE-20261005-weekend`), and the standing service is suppressed on those
  dates via a `calendar_dates.txt exception_type=2` row
  (`build_gtfs.suppressed_services_for`). This is checked twice,
  independently: once in Python (`test_build_gtfs.py`) and once in the Rust
  validator (`validator/src/main.rs`), which re-derives calendar activity
  from the built feed's own `calendar.txt`/`calendar_dates.txt` rather than
  trusting `build_gtfs.py`'s internal bookkeeping.
- **`trip_id` includes the revision name** (`SLE-<revision>-<trip_short_name>`,
  e.g. `SLE-20261005-weekday-1633`), not just `SLE-<trip_short_name>` as an
  early draft of section 4 proposed — necessary because the same printed
  trip number (e.g. `1633`) is reused across different revisions as "the
  same conceptual run," but GTFS requires `trip_id` to be unique feed-wide.
- **`sources/older_examples/`** (added by the project owner mid-implementation:
  an MLK-day PDF, three Thanksgiving-week PDFs, and an independent prior-year
  standing PDF) are used as **extraction regression tests only**
  (`test_pdf_extract.py::test_extract_older_examples`), not as additional
  `[[revision]]` entries in the shipped feed — their dates fall outside (MLK
  2026-01-19) or after (Thanksgiving 2025, predating this feed's 2026-03-29
  start) the feed's actual coverage window, and their true supersession
  history isn't something this project can know confidently. They did prove
  valuable as-is: extraction generalized to all of them without any
  PDF-specific code changes, including the letter-spacing and track-badge
  issues above, which is exactly what they were added to confirm (requirement
  #7 — a future real outage/holiday PDF should need a config change, not new
  parsing code).

## 13. Corrections after initial implementation

Two things the project owner corrected after reviewing the first working
build:

- **A `no_op` flag for exception revisions, after all.** The "every
  exception revision is a fully independent service, always, even when the
  schedule is unchanged" rule from section 0/12 was something the owner
  wanted *proven to work* on the real 2026-10-10/11 outage PDF, not
  necessarily shipped that way permanently. `config.toml`'s
  `20261010-11-outage` revision now has `no_op = true`: `build_gtfs.py`
  skips it entirely (no trips, no calendar_dates rows at all, standing
  service left alone) once a revision is flagged this way. The underlying
  mechanism — independent trip_ids/stop_times under their own service_id,
  plus `exception_type=2` suppression of whatever standing service would
  otherwise cover those dates — is unchanged and still fully covered by
  tests; it's just not exercised by the shipped config's default revision
  list anymore. See `test_build_gtfs.py::test_non_noop_exception_revision_*`
  (forces the flag off against the real extracted data) and
  `::test_noop_exception_revision_produces_nothing` (confirms the flagged
  revision is actually inert in the built feed).
- **Stop IDs/codes for the 7 MNR-shared stations now come from MNR, not
  Amtrak or an invented code.** Sections 3/5's original `SLE-<3-letter-code>`
  scheme sourced those codes from Amtrak's stop_id (or invented one, for New
  Haven-State Street and the 3 stations Amtrak doesn't serve) purely for
  human readability, with no particular reasoning about which upstream
  system's code to prefer. The owner wants MNR's own `stop_code` (from
  `gtfsmnr/stops.txt`, e.g. `2SM` for Stamford, `2NH` for New Haven) used
  instead for every station MNR also serves — `stop_id` becomes `SLE-2SM`
  etc., and a new `stop_code` column on `stops.txt` carries the bare code
  (`2SM`) directly. The motivation: a future tool converting MNR's realtime
  feed into GTFS-RT for this feed can match on `stop_code` against MNR's own
  codes with no separate translation table. The 7 SLE-only stations (no MNR
  service) are unaffected in `stop_id`, and gained a `stop_code` column too
  (populated with their Amtrak stop_id, e.g. `NLC`) for consistency even
  though no realtime-matching need was stated for those. Changing `stop_id`
  required re-running and re-promoting every `extracted/*.csv` (the station
  lookup is baked in at extraction time, not resolved later) — see
  `gtfs_builder/config.toml`'s stops section for the full station-by-station
  code table.

## 14. Correction: MNR's feed *does* contain the thru-trains

Section 0/12's claim that "Metro-North's own GTFS doesn't contain the SLE
thru-trains at all" was **wrong**, and worth recording exactly how, since the
mistake itself is instructive. The original check was `grep -i "SLE\|Shore"
gtfsmnr/trips.txt gtfs_mnr/routes.txt` — zero matches, taken as proof. That
was never going to find anything: MNR's own feed has no reason to label a
train it operates "SLE" internally; it would just appear as an ordinary
numbered trip on MNR's own route. Searching for the actual train numbers
instead finds it immediately:

```
3,246131924613651,246131924613651+2358187+1,Stamford,1633,1,,5,0,1
3,246131924613651,246131924613651+2358191+0,New Haven-State St,1638,0,,16,0,0
```

— and that trip's `stop_times.txt` matches our own PDF-extracted times for
1633's New Haven→Stamford leg to the minute, including the 6:55 departure
from New Haven.

**Why this doesn't change the shipped design anyway**, once investigated
further: MNR's trip_id for "1633"/"1638" is not one stable identifier — MNR
publishes a *different* trip_id per calendar date (part of a day-of-week
equipment/crew diagram rotation; 41 distinct trip_ids were found just for
`trip_short_name=1638` in the one snapshot in `sources/gtfsmnr/`, e.g. one
service_id covers only Mondays Oct 5–Nov 16, a different one only Tuesdays,
etc.) — and that snapshot's own calendar_dates only goes back to about
2026-10-01, so it couldn't help with the Mar 29 revision regardless. Reusing
MNR's literal trip_id would mean either re-fetching a live, current MNR
export on every build (contradicting the owner's "the converter probably
shouldn't read the Amtrak or MNR GTFS feeds" instruction) or modeling SLE's
own trips at MNR's day-by-day granularity, which the public SLE timetable
doesn't even distinguish. The owner confirmed after seeing this: don't
pursue MNR trip_id/stop_id merging after all — the self-contained `SLE-*`
trip_id scheme and the (separately motivated, section 13) MNR-`stop_code`
reuse stand as shipped. Stop_id-level reuse remains real and stable (the
station is the same place regardless of which specific date's trip_id names
it); trip_id-level reuse was the part that turned out impractical, for
reasons beyond "MNR doesn't have it" — MNR does, it's just not stable.

## 15. Track-badge OCR correctness bug, found via the 2026-10-12 outage PDF

CTrail published a third outage-notice PDF,
`SLE-10_12-Portal-North-Bridge-MNR-WALK-Bridge-Outage.pdf` (effective
2026-10-12, Columbus Day — a weekday, unlike the 10/10-11 weekend one).
Extracting it to compare against the standing `20261005-weekday` revision
surfaced 20 apparent track-assignment differences at Branford/Guilford
(flipping between tracks 1 and 4). All 20 turned out to be OCR misreads in
the *new* PDF's extraction, not real differences — confirmed by cropping and
reading the actual rendered badges by eye, every one of which matched the
standing schedule exactly. Comparing against this same-era PDF is what
surfaced it, not anything specific to this PDF being wrong.

**Investigating further found this was not a one-off**: the same "1
misread as 4 (or 7)" failure was already present, silently, in the
*previously shipped* `20260329-weekday.csv` and `20261005-weekend.csv` —
both had been promoted during earlier implementation sessions without this
being caught, despite the project's own "verify every cell against the
rendered PDF" policy. A systematic re-check (cropping and reading every
track badge in all three weekday and all three weekend PDFs by eye, then
cross-validating that every PDF pair agreed on every shared trip/station)
found and fixed all of it — see the station-by-station tables carried in
this session's transcript if re-deriving this is ever needed again; the
corrected values are now what's checked into `extracted/*.csv`.

**Root cause**: `ocr_track_badge`'s original default render resolution
(400dpi) was too low to reliably distinguish "1" from "4" for this specific
badge glyph/arrow-icon combination on at least two of the PDFs — confirmed
by re-cropping the identical bounding box at 600dpi, which read correctly
every time. A digit-only tesseract whitelist was also added (free-form OCR
had been reading the arrow glyph after the digit as extra spurious digits).
**Neither fix alone was sufficient and OCR here is not fully deterministic**:
even with the whitelist and resolution fix, a single tesseract invocation
occasionally still returns the wrong digit (or empty text) on some
badge/PDF combinations, and running the *exact same code against the exact
same PDF* has been observed to produce different results across separate
invocations — real nondeterminism, not environment-specific flakiness. The
shipped mitigation is a majority vote across several resolution/psm
combinations (`OCR_ATTEMPTS` in `pdf_extract.py`), which is a real
improvement but explicitly **not** treated as sufficient on its own — see
below.

**Consequence for how this pipeline is tested and trusted**: given OCR
non-determinism is real, any test or process that needs a *fact* about a
schedule (e.g. "this outage PDF is identical to the standing one") now reads
the already-promoted `extracted/*.csv` files rather than re-running
extraction live — see `test_pdf_extract.py`'s module docstring and
`test_noop_outage_matches_standing_schedule_exactly`. Live re-extraction in
tests is now limited to checks that don't depend on any individual OCR'd
digit being right (trip/stop *counts*, which only change if row/column
detection structurally breaks) and to `sources/older_examples/`, which has
no promoted-CSV equivalent to compare against. This is also a reminder for
future revisions: **promotion must mean actually looking at the rendered
PDF**, not trusting that extraction "usually works" — exactly the failure
mode the sibling MARC project's own lessons document warned about, which
this project itself fell into once before being corrected.

`20261012-outage` itself, once its track data was hand-corrected to match
manual verification, turned out byte-identical to the standing
`20261005-weekday` revision (same trips, times, and tracks — only the MNR
connection columns differ, and this PDF drops the AMTK connecting-train
columns entirely rather than showing them blank). It's configured
`no_op = true`, same as `20261010-11-outage`.
