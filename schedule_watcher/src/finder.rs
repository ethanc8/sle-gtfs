use scraper::{Html, Selector};
use url::Url;

use crate::error::{Error, Result};

/// The page CTrail Shore Line East publishes its current and
/// exception-date schedule PDFs on.
pub const DEFAULT_PAGE_URL: &str = "https://shorelineeast.com/schedules/";

/// CSS selector scoping link discovery to the "Schedule and Track
/// Assignments" section specifically, rather than the whole page (which
/// also links unrelated things like connecting-agency homepages). Confirmed
/// present on the page as of 2026-10-03 -- see `references/2026-10-03.html`.
/// If this container is ever removed/renamed in a page redesign,
/// [`find_schedule_pdf_links_in_html`] falls back to a whole-page scan
/// rather than silently finding nothing (see its doc comment).
pub const SCHEDULE_CONTAINER_SELECTOR: &str = "#holidaySchedule";

/// A PDF link found on the schedules page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PdfLink {
    /// Absolute URL of the PDF.
    pub url: String,
    /// The link's visible text (e.g. "Download the current PDF schedule"),
    /// trimmed. Not currently used for filtering (unlike the pace
    /// downloader's "gtfs" keyword check) since every PDF link found on this
    /// page so far has been a real schedule -- kept around for whoever next
    /// needs to add a filter once that's no longer true.
    pub text: String,
}

/// Extract PDF links from a set of already-selected anchor elements,
/// resolving relative hrefs against `base` and deduping by resolved URL.
fn pdf_links_from_elements<'a>(
    elements: impl Iterator<Item = scraper::ElementRef<'a>>,
    base: &Url,
    page_url: &str,
) -> Result<Vec<PdfLink>> {
    let mut links = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for element in elements {
        let href = element.value().attr("href").unwrap_or_default();
        let href_no_query = href.split(['?', '#']).next().unwrap_or(href);
        if !href_no_query.to_ascii_lowercase().ends_with(".pdf") {
            continue;
        }
        let resolved = base.join(href).map_err(|source| Error::UrlParse {
            href: href.to_string(),
            page_url: page_url.to_string(),
            source,
        })?;
        let url = resolved.to_string();
        if !seen.insert(url.clone()) {
            continue; // same PDF linked more than once in this scope
        }
        let text: String = element.text().collect::<Vec<_>>().join(" ").trim().to_string();
        links.push(PdfLink { url, text });
    }
    Ok(links)
}

/// Find every schedule PDF link inside already-fetched HTML.
///
/// Tries [`SCHEDULE_CONTAINER_SELECTOR`] first. If that scope yields zero
/// PDF links (e.g. the page was redesigned and the container's `id`
/// changed), falls back to every `.pdf` link on the whole page instead of
/// silently reporting nothing -- missing a real schedule change is worse
/// than one run with a few extra false-positive candidates, and a warning
/// is printed to stderr when this happens so it doesn't go unnoticed.
/// Only if the whole-page fallback *also* finds nothing is this an error.
pub fn find_schedule_pdf_links_in_html(html: &str, page_url: &str) -> Result<Vec<PdfLink>> {
    let base = Url::parse(page_url).map_err(|source| Error::UrlParse {
        href: page_url.to_string(),
        page_url: page_url.to_string(),
        source,
    })?;
    let document = Html::parse_document(html);

    let scoped_selector = Selector::parse(SCHEDULE_CONTAINER_SELECTOR)
        .expect("static selector is valid");
    let container_exists = document.select(&scoped_selector).next().is_some();
    let link_selector = Selector::parse("a[href]").expect("static selector is valid");

    if container_exists {
        let anchors_in_container = document
            .select(&scoped_selector)
            .flat_map(|container| container.select(&link_selector).collect::<Vec<_>>());
        let links = pdf_links_from_elements(anchors_in_container, &base, page_url)?;
        if !links.is_empty() {
            return Ok(links);
        }
        eprintln!(
            "warning: {SCHEDULE_CONTAINER_SELECTOR} is present on {page_url} but contains no PDF \
             links; falling back to a whole-page scan (results may include unrelated PDFs)"
        );
    } else {
        eprintln!(
            "warning: expected container {SCHEDULE_CONTAINER_SELECTOR} not found on {page_url} \
             (page redesign?); falling back to a whole-page scan (results may include unrelated PDFs)"
        );
    }

    let whole_page = pdf_links_from_elements(document.select(&link_selector), &base, page_url)?;
    if whole_page.is_empty() {
        eprintln!("error: no schedule PDF links were found on {page_url}");
        return Err(Error::NoPdfLinksFound {
            page_url: page_url.to_string(),
        });
    }
    Ok(whole_page)
}

/// Fetch `page_url` over the network and find every schedule PDF link on it.
pub fn fetch_schedule_pdf_links(page_url: &str) -> Result<Vec<PdfLink>> {
    let response = reqwest::blocking::get(page_url).map_err(|source| Error::Fetch {
        url: page_url.to_string(),
        source,
    })?;
    let status = response.status();
    if !status.is_success() {
        return Err(Error::HttpStatus {
            url: page_url.to_string(),
            status: status.as_u16(),
        });
    }
    let html = response.text().map_err(|source| Error::Fetch {
        url: page_url.to_string(),
        source,
    })?;
    find_schedule_pdf_links_in_html(&html, page_url)
}

/// Find every schedule PDF link currently on CTrail Shore Line East's
/// schedules page.
pub fn find_current_schedule_pdf_links() -> Result<Vec<PdfLink>> {
    fetch_schedule_pdf_links(DEFAULT_PAGE_URL)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reference(name: &str) -> String {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/references/").to_string() + name;
        std::fs::read_to_string(path).expect("reference file should exist")
    }

    #[test]
    fn finds_the_three_links_present_on_2026_10_03() {
        let html = reference("2026-10-03.html");
        let links = find_schedule_pdf_links_in_html(&html, DEFAULT_PAGE_URL).unwrap();
        let urls: Vec<&str> = links.iter().map(|l| l.url.as_str()).collect();
        assert_eq!(
            urls,
            vec![
                "https://shorelineeast.com/wp-content/uploads/2026/03/SLE-3_29-MNR-Schedule-Change.pdf",
                "https://shorelineeast.com/wp-content/uploads/2026/09/SLE-October-5th-schedule_edit.pdf",
                "https://shorelineeast.com/wp-content/uploads/2026/10/SLE-10_10-Portal-North-Bridge-MNR-WALK-Bridge-Outage-1.pdf",
            ]
        );
        assert_eq!(links[0].text, "Download the current PDF schedule");
    }

    #[test]
    fn errors_when_no_pdf_link_present_anywhere() {
        let html = "<html><body><p>no downloads here</p></body></html>";
        let err = find_schedule_pdf_links_in_html(html, DEFAULT_PAGE_URL).unwrap_err();
        assert!(matches!(err, Error::NoPdfLinksFound { .. }));
    }

    #[test]
    fn falls_back_to_whole_page_when_container_missing() {
        let html = r#"<html><body><a href="/files/some-schedule.pdf">Download</a></body></html>"#;
        let links = find_schedule_pdf_links_in_html(html, DEFAULT_PAGE_URL).unwrap();
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].url, "https://shorelineeast.com/files/some-schedule.pdf");
    }

    #[test]
    fn falls_back_to_whole_page_when_container_present_but_empty() {
        let html = r#"<html><body>
            <div id="holidaySchedule"><p>no links in here right now</p></div>
            <a href="/files/elsewhere.pdf">Elsewhere</a>
        </body></html>"#;
        let links = find_schedule_pdf_links_in_html(html, DEFAULT_PAGE_URL).unwrap();
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].url, "https://shorelineeast.com/files/elsewhere.pdf");
    }

    #[test]
    fn ignores_non_pdf_links_and_dedupes_repeated_ones() {
        let html = r#"<html><body><div id="holidaySchedule">
            <a href="https://www.hartfordline.com/">Hartford Line</a>
            <a href="/files/a.pdf">A</a>
            <a href="/files/a.pdf">A again</a>
            <a href="/files/b.PDF">B (uppercase extension)</a>
        </div></body></html>"#;
        let links = find_schedule_pdf_links_in_html(html, DEFAULT_PAGE_URL).unwrap();
        let urls: Vec<&str> = links.iter().map(|l| l.url.as_str()).collect();
        assert_eq!(
            urls,
            vec![
                "https://shorelineeast.com/files/a.pdf",
                "https://shorelineeast.com/files/b.PDF",
            ]
        );
    }
}
