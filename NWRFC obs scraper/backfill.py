#!/usr/bin/env python3
"""
backfill.py
-----------
Fetch and store NWRFC runoff data for historical dates by hitting the
?date=MM/DD/YYYY URL on the runoff summary page.

Backfill is INSERT-OR-IGNORE: rows already present in the DB (from the daily
scraper or previous backfills) are left untouched. Only missing dates get new
rows. Re-running is safe and idempotent.

Usage:
    # Default: backfill all missing days from Oct 1 of current WY → today
    python3 backfill.py

    # Explicit date range
    python3 backfill.py --start 2025-10-01 --end 2026-03-31

    # Force-fetch every date in range (skip the gap-detection optimization)
    python3 backfill.py --start 2026-04-01 --no-skip-existing
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import NWRFC_URL
from database import (
    create_tables,
    get_connection,
    insert_observation_if_missing,
    log_scrape,
)
from scraper import fetch_page, parse_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DATE_PARAM_FORMAT = "%m/%d/%Y"   # Confirmed working URL format (e.g. 03/15/2026)
DELAY_SECONDS     = 3            # Polite pause between requests


def water_year_start(for_date: date) -> date:
    """Return Oct 1 of the water year that contains for_date."""
    if for_date.month >= 10:
        return date(for_date.year, 10, 1)
    return date(for_date.year - 1, 10, 1)


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def existing_dates(conn: sqlite3.Connection, start: date, end: date) -> set[str]:
    """Return the set of obs_dates already in the DB within [start, end]."""
    rows = conn.execute("""
        SELECT DISTINCT obs_date FROM runoff_observations
        WHERE obs_date >= ? AND obs_date <= ?
    """, (start.isoformat(), end.isoformat())).fetchall()
    return {r[0] for r in rows}


def main():
    parser = argparse.ArgumentParser(description="Backfill NWRFC runoff data")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: Oct 1 of current WY)")
    parser.add_argument("--end",   help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Fetch every date in range even if already in DB "
                             "(values still won't overwrite existing rows)")
    args = parser.parse_args()

    today = date.today()
    end_date   = date.fromisoformat(args.end)   if args.end   else today
    start_date = date.fromisoformat(args.start) if args.start else water_year_start(today)

    logger.info(f"Backfill range: {start_date} → {end_date}")

    conn = get_connection()
    create_tables(conn)

    have = existing_dates(conn, start_date, end_date)
    all_dates = list(date_range(start_date, end_date))
    if args.no_skip_existing:
        targets = all_dates
        logger.info(f"Forcing all {len(targets)} dates "
                    f"({len(have)} already in DB will be re-checked but not overwritten)")
    else:
        targets = [d for d in all_dates if d.isoformat() not in have]
        logger.info(f"{len(have)} dates already in DB, "
                    f"{len(targets)} to fetch (out of {len(all_dates)} in range)")

    if not targets:
        logger.info("Nothing to backfill — DB is already complete for this range.")
        conn.close()
        return 0

    summary = {"fetched": 0, "rows_new": 0, "rows_skipped": 0, "errors": 0, "no_data": 0}

    for d in targets:
        date_str = d.strftime(DATE_PARAM_FORMAT)
        url = f"{NWRFC_URL}?date={date_str}"
        run_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"Fetching {d.isoformat()} …")
        try:
            html = fetch_page(url)
            records = parse_page(html)
        except Exception as exc:
            logger.warning(f"  Failed for {d}: {exc}")
            log_scrape(conn, run_at, d.isoformat(), 0, "error", str(exc))
            summary["errors"] += 1
            time.sleep(DELAY_SECONDS)
            continue

        if not records:
            logger.warning(f"  No records returned for {d}")
            summary["no_data"] += 1
            time.sleep(DELAY_SECONDS)
            continue

        # Confirm NWRFC actually returned data for the requested date.
        returned_date = records[0].get("obs_date", "").replace("/", "-")
        if returned_date != d.isoformat():
            logger.warning(f"  Date mismatch: requested {d.isoformat()}, "
                           f"page returned {returned_date} — skipping")
            time.sleep(DELAY_SECONDS)
            continue

        new = skipped = 0
        for record in records:
            if insert_observation_if_missing(conn, record):
                new += 1
            else:
                skipped += 1
        conn.commit()
        log_scrape(conn, run_at, returned_date, new, "success",
                   f"backfill: {new} new, {skipped} preserved")
        logger.info(f"  +{new} new rows, {skipped} preserved")

        summary["fetched"]      += 1
        summary["rows_new"]     += new
        summary["rows_skipped"] += skipped
        time.sleep(DELAY_SECONDS)

    conn.close()
    logger.info(
        f"Done. Fetched {summary['fetched']} pages | "
        f"{summary['rows_new']:,} new rows | "
        f"{summary['rows_skipped']:,} preserved | "
        f"{summary['errors']} errors | {summary['no_data']} no-data"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
