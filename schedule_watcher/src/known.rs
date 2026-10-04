use std::collections::HashSet;
use std::path::Path;

use crate::error::{Error, Result};
use crate::finder::PdfLink;

/// A PDF link found on the schedules page whose filename doesn't match any
/// PDF already present (at any depth) under the directory `known_pdf_basenames`
/// was given.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewPdf {
    pub url: String,
    pub filename: String,
    pub text: String,
}

/// The final path segment of a URL, used as the filename to compare/save as.
pub fn url_filename(url: &str) -> Result<String> {
    let parsed = url::Url::parse(url).map_err(|source| Error::UrlParse {
        href: url.to_string(),
        page_url: url.to_string(),
        source,
    })?;
    parsed
        .path_segments()
        .and_then(|mut segments| segments.next_back())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .ok_or_else(|| Error::NoFilenameInUrl { url: url.to_string() })
}

/// Every `*.pdf` filename (case-insensitive) found anywhere under `dir`,
/// searched recursively -- this repo keeps already-downloaded PDFs both
/// directly in `sources/` and in `sources/older_examples/`, and a PDF in
/// either place counts as "already known to this repo", not just ones
/// referenced by `gtfs_builder/config.toml`'s `[[revision]]` list (a PDF can
/// be downloaded and under human review before it's wired into a revision).
pub fn known_pdf_basenames(dir: &Path) -> Result<HashSet<String>> {
    let mut found = HashSet::new();
    collect_pdf_basenames(dir, &mut found)?;
    Ok(found)
}

fn collect_pdf_basenames(dir: &Path, found: &mut HashSet<String>) -> Result<()> {
    let entries = std::fs::read_dir(dir).map_err(|source| Error::ReadDir {
        path: dir.to_path_buf(),
        source,
    })?;
    for entry in entries {
        let entry = entry.map_err(|source| Error::ReadDir {
            path: dir.to_path_buf(),
            source,
        })?;
        let path = entry.path();
        if path.is_dir() {
            collect_pdf_basenames(&path, found)?;
            continue;
        }
        if let Some(name) = path.file_name().and_then(|n| n.to_str())
            && name.to_ascii_lowercase().ends_with(".pdf")
        {
            found.insert(name.to_string());
        }
    }
    Ok(())
}

/// Which of `links` has a filename not present in `known` (case-insensitive
/// comparison, since web servers and local filesystems don't always agree
/// on case-sensitivity). Preserves the order `links` was given in.
pub fn find_new_pdfs(links: &[PdfLink], known: &HashSet<String>) -> Result<Vec<NewPdf>> {
    let known_lower: HashSet<String> = known.iter().map(|s| s.to_ascii_lowercase()).collect();
    let mut new_pdfs = Vec::new();
    for link in links {
        let filename = url_filename(&link.url)?;
        if !known_lower.contains(&filename.to_ascii_lowercase()) {
            new_pdfs.push(NewPdf {
                url: link.url.clone(),
                filename,
                text: link.text.clone(),
            });
        }
    }
    Ok(new_pdfs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn link(url: &str) -> PdfLink {
        PdfLink { url: url.to_string(), text: String::new() }
    }

    #[test]
    fn url_filename_extracts_last_path_segment() {
        assert_eq!(url_filename("https://x.com/a/b/Schedule.pdf").unwrap(), "Schedule.pdf");
    }

    #[test]
    fn known_pdf_basenames_is_recursive() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("A.pdf"), b"").unwrap();
        let sub = dir.path().join("older_examples");
        fs::create_dir(&sub).unwrap();
        fs::write(sub.join("B.pdf"), b"").unwrap();
        fs::write(dir.path().join("not-a-pdf.txt"), b"").unwrap();

        let found = known_pdf_basenames(dir.path()).unwrap();
        assert_eq!(found, HashSet::from(["A.pdf".to_string(), "B.pdf".to_string()]));
    }

    #[test]
    fn find_new_pdfs_reports_only_unknown_filenames_case_insensitively() {
        let links = vec![
            link("https://x.com/Known.pdf"),
            link("https://x.com/NEW-ONE.pdf"),
        ];
        let known = HashSet::from(["known.pdf".to_string()]);
        let new_pdfs = find_new_pdfs(&links, &known).unwrap();
        assert_eq!(new_pdfs.len(), 1);
        assert_eq!(new_pdfs[0].filename, "NEW-ONE.pdf");
    }

    #[test]
    fn find_new_pdfs_empty_when_everything_known() {
        let links = vec![link("https://x.com/a.pdf")];
        let known = HashSet::from(["a.pdf".to_string()]);
        assert!(find_new_pdfs(&links, &known).unwrap().is_empty());
    }
}
