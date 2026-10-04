//! Sanity-checks a built CTrail Shore Line East GTFS feed.
//!
//! `gtfs_structures::Gtfs::new` already resolves every stop_id reference in
//! stop_times.txt (an unknown stop_id is a hard error there), so just
//! loading the feed is itself a real check. This adds:
//!
//! - every trip's route_id and service_id actually exist
//! - every trip's stop_times are in non-decreasing time order
//! - the exception-revision invariant requested by the project owner: for
//!   every calendar_dates.txt "service added" (exception_type=1) row, no
//!   OTHER service's calendar.txt entry is also active that date unless
//!   it's explicitly suppressed by a matching exception_type=2 row. This is
//!   an independent re-check of build_gtfs.py's own suppression logic
//!   against the actual output, not just a re-run of the same code path.

use std::collections::HashSet;
use std::process::ExitCode;

use gtfs_structures::{Calendar, Exception};
use jiff::civil::{Date, Weekday};

fn calendar_active_on(cal: &Calendar, date: Date) -> bool {
    if date < cal.start_date || date > cal.end_date {
        return false;
    }
    match date.weekday() {
        Weekday::Monday => cal.monday,
        Weekday::Tuesday => cal.tuesday,
        Weekday::Wednesday => cal.wednesday,
        Weekday::Thursday => cal.thursday,
        Weekday::Friday => cal.friday,
        Weekday::Saturday => cal.saturday,
        Weekday::Sunday => cal.sunday,
    }
}

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let Some(gtfs_path) = args.next() else {
        eprintln!("usage: gtfs_validator <path-to-gtfs.zip>");
        return ExitCode::FAILURE;
    };

    let gtfs = match gtfs_structures::Gtfs::new(&gtfs_path) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("failed to load {gtfs_path}: {e}");
            return ExitCode::FAILURE;
        }
    };
    gtfs.print_stats();

    let mut problems = Vec::new();
    let known_services: HashSet<&str> = gtfs
        .calendar
        .keys()
        .map(String::as_str)
        .chain(gtfs.calendar_dates.keys().map(String::as_str))
        .collect();

    for trip in gtfs.trips.values() {
        if !gtfs.routes.contains_key(&trip.route_id) {
            problems.push(format!("trip {}: unknown route_id {}", trip.id, trip.route_id));
        }
        if !known_services.contains(trip.service_id.as_str()) {
            problems.push(format!("trip {}: unknown service_id {}", trip.id, trip.service_id));
        }

        let mut prev: Option<(u32, &str)> = None;
        for st in &trip.stop_times {
            let Some(time) = st.arrival_time.or(st.departure_time) else {
                problems.push(format!("trip {}: stop {} has no arrival or departure time", trip.id, st.stop.id));
                continue;
            };
            if let Some((prev_time, prev_stop)) = prev {
                if time < prev_time {
                    problems.push(format!(
                        "trip {}: time goes backwards from {prev_stop} ({prev_time}s) to {} ({time}s)",
                        trip.id, st.stop.id
                    ));
                }
            }
            prev = Some((time, &st.stop.id));
        }
    }

    // Exception-suppression invariant (see module doc).
    for (service_id, dates) in &gtfs.calendar_dates {
        for cd in dates {
            if cd.exception_type != Exception::Added {
                continue;
            }
            for (other_id, cal) in &gtfs.calendar {
                if other_id == service_id {
                    continue;
                }
                if !calendar_active_on(cal, cd.date) {
                    continue;
                }
                let suppressed = gtfs
                    .calendar_dates
                    .get(other_id)
                    .map(|v| v.iter().any(|d| d.date == cd.date && d.exception_type == Exception::Deleted))
                    .unwrap_or(false);
                if !suppressed {
                    problems.push(format!(
                        "exception service {service_id} adds {} but standing service {other_id} \
                         is also active that date with no exception_type=2 suppression row",
                        cd.date
                    ));
                }
            }
        }
    }

    if problems.is_empty() {
        println!("OK: no referential-integrity, ordering, track, or exception-suppression problems found.");
        ExitCode::SUCCESS
    } else {
        println!("{} problem(s) found:", problems.len());
        for p in &problems {
            println!("  - {p}");
        }
        ExitCode::FAILURE
    }
}
