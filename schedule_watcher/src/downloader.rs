use std::path::{Path, PathBuf};

use crate::error::{Error, Result};
use crate::known::url_filename;

/// Outcome of downloading one new PDF.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadOutcome {
    pub url: String,
    pub path: PathBuf,
    pub bytes_written: u64,
}

/// Download the PDF at `url` into `dir`, named after the URL's final path
/// segment. `dir` is created if it doesn't exist.
pub fn download_pdf_to_dir(url: &str, dir: &Path) -> Result<DownloadOutcome> {
    let filename = url_filename(url)?;
    let dest_path = dir.join(filename);

    let response = reqwest::blocking::get(url).map_err(|source| Error::Fetch {
        url: url.to_string(),
        source,
    })?;
    let status = response.status();
    if !status.is_success() {
        return Err(Error::HttpStatus {
            url: url.to_string(),
            status: status.as_u16(),
        });
    }
    let bytes = response.bytes().map_err(|source| Error::Fetch {
        url: url.to_string(),
        source,
    })?;

    std::fs::create_dir_all(dir).map_err(|source| Error::Io {
        path: dir.to_path_buf(),
        source,
    })?;
    std::fs::write(&dest_path, &bytes).map_err(|source| Error::Io {
        path: dest_path.clone(),
        source,
    })?;

    Ok(DownloadOutcome {
        url: url.to_string(),
        path: dest_path,
        bytes_written: bytes.len() as u64,
    })
}
