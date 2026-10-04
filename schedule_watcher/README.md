# sle_schedule_watcher

Watches <https://shorelineeast.com/schedules/> for schedule PDF links this
repo hasn't seen yet. CTrail doesn't announce new Shore Line East timetables
anywhere machine-readable, and the schedules page's links change URL (and
sometimes just get replaced in place) with no RSS/changelog -- this tool
re-checks the page instead of requiring someone to look at it by hand. See
the sibling project `pace-gtfs-downloader` for the same idea applied to a
single canonical feed URL instead of a list of PDFs to diff.

It ships as both a Rust library crate (`sle_schedule_watcher`) and a CLI
binary (`sle-schedule-watcher`) built from the same crate.

## Building

```sh
cargo build --release
# binary at target/release/sle-schedule-watcher
```

Run the test suite (parses the page snapshot in `references/` and exercises
the known-PDF diff logic against temp directories — no network access
required):

```sh
cargo test
```

## How PDF discovery works

The finder fetches the schedules page and looks for `.pdf`-ending `<a href>`
links inside the `#holidaySchedule` container (the "Schedule and Track
Assignments" section specifically — confirmed present as of 2026-10-03, see
`references/2026-10-03.html`), which avoids picking up unrelated PDFs
elsewhere on the page. If that container is missing or contains no PDF
links (e.g. after a page redesign), it falls back to every `.pdf` link on
the whole page and prints a warning to stderr, rather than silently
reporting nothing — see `src/finder.rs`'s doc comment for the full
reasoning.

"New" is determined by filename only (the final path segment of the URL,
compared case-insensitively) against every `*.pdf` found recursively under
a given directory — by default `sources/` at the repo root, which covers
both the currently-active PDFs and `sources/older_examples/`.

## CLI usage

```
sle-schedule-watcher <COMMAND>

Commands:
  list           Print every schedule PDF link currently on the page
  check          Compare against sources/ and report anything new (exit 1 if so)
  download-new   Like check, but also downloads each new PDF
```

### `list`

Prints every link found as `<url>\t<link text>`.

```sh
$ sle-schedule-watcher list
https://shorelineeast.com/wp-content/uploads/2026/03/SLE-3_29-MNR-Schedule-Change.pdf	Download the current PDF schedule
https://shorelineeast.com/wp-content/uploads/2026/09/SLE-October-5th-schedule_edit.pdf	Download the PDF schedule
https://shorelineeast.com/wp-content/uploads/2026/10/SLE-10_10-Portal-North-Bridge-MNR-WALK-Bridge-Outage-1.pdf	Download the PDF schedule
```

### `check [--sources-dir DIR]`

Compares the page's links against `DIR` (default `sources`, searched
recursively) and reports any whose filename isn't already present anywhere
under it.

```sh
$ sle-schedule-watcher check
no new schedule PDFs found (compared against sources)
$ echo $?
0

$ sle-schedule-watcher check
2 new schedule PDF(s) found on https://shorelineeast.com/schedules/ not present under sources:
  https://shorelineeast.com/wp-content/uploads/2026/12/SLE-Holiday-Schedule.pdf (Download the PDF schedule)
  https://shorelineeast.com/wp-content/uploads/2027/01/SLE-Schedule-Change.pdf (Download the current PDF schedule)
$ echo $?
1
```

The nonzero exit code on "something new found" is deliberate, not an error
condition — `check` is meant to be used directly as a CI/cron alert
condition (see `.github/workflows/check-new-schedules.yml` in the repo
root), the same way `diff` exits 1 for "files differ."

### `download-new [--sources-dir DIR] [--download-to DIR]`

Same comparison as `check`, but downloads each new PDF (named after its URL's
filename) into `--download-to` (default: same as `--sources-dir`). Always
exits 0 — meant to be run unconditionally rather than branched on.

```sh
$ sle-schedule-watcher download-new
downloaded 441823 bytes from https://shorelineeast.com/wp-content/uploads/2026/12/SLE-Holiday-Schedule.pdf to sources/SLE-Holiday-Schedule.pdf
```

Note this only downloads the PDF — it does not extract it, add a
`[[revision]]` entry, or otherwise touch the GTFS pipeline. See
`UPDATING.md` in the repo root for what to do next.

## Rust API

```toml
[dependencies]
sle_schedule_watcher = { path = "../schedule_watcher" }
```

```rust
use sle_schedule_watcher::{find_current_schedule_pdf_links, find_new_pdfs, known_pdf_basenames};
use std::path::Path;

let links = find_current_schedule_pdf_links()?;
let known = known_pdf_basenames(Path::new("sources"))?;
let new_pdfs = find_new_pdfs(&links, &known)?;
for pdf in &new_pdfs {
    println!("new: {} ({})", pdf.filename, pdf.url);
}
```

See `src/lib.rs` for the full list of re-exported functions/types and
`src/error.rs` for the `Error` enum.

## Repository layout

- `src/finder.rs` — PDF link discovery (HTML parsing + fetch).
- `src/known.rs` — recursive directory scan + new-vs-known diff.
- `src/downloader.rs` — single-PDF download.
- `src/error.rs` — shared `Error`/`Result` types.
- `src/lib.rs` — public API re-exports.
- `src/main.rs` — CLI (`clap`-based).
- `references/` — a historical snapshot of the schedules page, used by the
  test suite.
