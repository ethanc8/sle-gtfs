use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

use sle_schedule_watcher::{
    download_pdf_to_dir, find_current_schedule_pdf_links, find_new_pdfs, known_pdf_basenames,
};

#[derive(Parser)]
#[command(
    name = "sle-schedule-watcher",
    about = "Watch shorelineeast.com/schedules/ for PDF links this repo hasn't converted yet"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Print every schedule PDF link currently on the page.
    List,
    /// Compare the page's PDF links against a directory of already-known
    /// PDFs (default: ./sources, searched recursively) and report any new
    /// ones. Exits 1 (not an error -- see README) if any are found, so this
    /// is usable directly as a CI/cron alert condition.
    Check {
        /// Directory to compare against (searched recursively for *.pdf).
        #[arg(long, default_value = "sources")]
        sources_dir: PathBuf,
    },
    /// Like `check`, but also downloads each new PDF into a directory.
    /// Exits 0 whether or not anything new was found (it's meant to be run
    /// unconditionally, e.g. on a schedule) -- use `check` first if you want
    /// a nonzero exit specifically when something new shows up.
    DownloadNew {
        /// Directory to compare against (searched recursively for *.pdf).
        #[arg(long, default_value = "sources")]
        sources_dir: PathBuf,
        /// Directory to save newly-found PDFs into (default: same as
        /// --sources-dir).
        #[arg(long)]
        download_to: Option<PathBuf>,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();

    match cli.command {
        Command::List => match find_current_schedule_pdf_links() {
            Ok(links) => {
                for link in links {
                    println!("{}\t{}", link.url, link.text);
                }
                ExitCode::SUCCESS
            }
            Err(err) => {
                eprintln!("error: {err}");
                ExitCode::FAILURE
            }
        },

        Command::Check { sources_dir } => run_check(&sources_dir),

        Command::DownloadNew { sources_dir, download_to } => {
            let download_dir = download_to.unwrap_or_else(|| sources_dir.clone());
            let (links, known) = match fetch_and_scan(&sources_dir) {
                Ok(pair) => pair,
                Err(code) => return code,
            };
            let new_pdfs = match find_new_pdfs(&links, &known) {
                Ok(n) => n,
                Err(err) => {
                    eprintln!("error: {err}");
                    return ExitCode::FAILURE;
                }
            };
            if new_pdfs.is_empty() {
                println!("no new schedule PDFs found (compared against {})", sources_dir.display());
                return ExitCode::SUCCESS;
            }
            for new_pdf in &new_pdfs {
                match download_pdf_to_dir(&new_pdf.url, &download_dir) {
                    Ok(outcome) => println!(
                        "downloaded {} bytes from {} to {}",
                        outcome.bytes_written,
                        outcome.url,
                        outcome.path.display()
                    ),
                    Err(err) => {
                        eprintln!("error downloading {}: {err}", new_pdf.url);
                        return ExitCode::FAILURE;
                    }
                }
            }
            ExitCode::SUCCESS
        }
    }
}

fn fetch_and_scan(
    sources_dir: &PathBuf,
) -> std::result::Result<(Vec<sle_schedule_watcher::PdfLink>, std::collections::HashSet<String>), ExitCode> {
    let links = find_current_schedule_pdf_links().map_err(|err| {
        eprintln!("error: {err}");
        ExitCode::FAILURE
    })?;
    let known = known_pdf_basenames(sources_dir).map_err(|err| {
        eprintln!("error: {err}");
        ExitCode::FAILURE
    })?;
    Ok((links, known))
}

fn run_check(sources_dir: &PathBuf) -> ExitCode {
    let (links, known) = match fetch_and_scan(sources_dir) {
        Ok(pair) => pair,
        Err(code) => return code,
    };
    let new_pdfs = match find_new_pdfs(&links, &known) {
        Ok(n) => n,
        Err(err) => {
            eprintln!("error: {err}");
            return ExitCode::FAILURE;
        }
    };

    if new_pdfs.is_empty() {
        println!("no new schedule PDFs found (compared against {})", sources_dir.display());
        return ExitCode::SUCCESS;
    }

    println!(
        "{} new schedule PDF(s) found on {} not present under {}:",
        new_pdfs.len(),
        sle_schedule_watcher::DEFAULT_PAGE_URL,
        sources_dir.display()
    );
    for new_pdf in &new_pdfs {
        println!("  {} ({})", new_pdf.url, new_pdf.text);
    }
    ExitCode::from(1)
}
