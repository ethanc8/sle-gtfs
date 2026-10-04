//! Watches CTrail Shore Line East's schedules page
//! (<https://shorelineeast.com/schedules/>) for PDF links this repo hasn't
//! seen yet -- a new link there is a signal that `sources/` needs a new PDF
//! and `gtfs_builder/config.toml` needs a new `[[revision]]` entry (see
//! `UPDATING.md` in the repo root).
//!
//! Three entry points cover the crate's job:
//! - [`find_current_schedule_pdf_links`] -- every schedule PDF link currently
//!   on the page.
//! - [`find_new_pdfs`] -- which of those aren't already present (at any
//!   depth) under a given directory, e.g. `sources/`.
//! - [`download_pdf_to_dir`] -- download one newly-found PDF.
//!
//! Each returns a `Result` (see [`Error`]); on the "could not find any PDF
//! link at all" failure mode, an explanation is also printed to stderr
//! before the `Err` is returned, matching this crate's sibling
//! `pace-gtfs-downloader`'s convention.

mod downloader;
mod error;
mod finder;
mod known;

pub use downloader::{download_pdf_to_dir, DownloadOutcome};
pub use error::{Error, Result};
pub use finder::{
    fetch_schedule_pdf_links, find_current_schedule_pdf_links, find_schedule_pdf_links_in_html,
    PdfLink, DEFAULT_PAGE_URL, SCHEDULE_CONTAINER_SELECTOR,
};
pub use known::{find_new_pdfs, known_pdf_basenames, url_filename, NewPdf};
