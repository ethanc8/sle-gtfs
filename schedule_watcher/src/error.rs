use std::path::PathBuf;

/// Errors produced by this crate.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("failed to fetch {url}: {source}")]
    Fetch {
        url: String,
        #[source]
        source: reqwest::Error,
    },

    #[error("fetching {url} returned HTTP status {status}")]
    HttpStatus { url: String, status: u16 },

    #[error("no schedule PDF links were found on {page_url}, even after falling back to a whole-page scan")]
    NoPdfLinksFound { page_url: String },

    #[error("could not resolve link '{href}' found on {page_url} as a URL: {source}")]
    UrlParse {
        href: String,
        page_url: String,
        #[source]
        source: url::ParseError,
    },

    #[error("a PDF link's URL has no final path segment to use as a filename: {url}")]
    NoFilenameInUrl { url: String },

    #[error("failed to read directory {path}: {source}")]
    ReadDir {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("failed to create/open {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

pub type Result<T> = std::result::Result<T, Error>;
