#!/usr/bin/env python3
"""
run_daily.py
------------
Daily entry point.  Scrapes the NWRFC page, writes to the database,
then exports today's key CSV snapshot to daily_results/.

Run directly or via launchd (installed by setup_launchd.sh):
    python3 run_daily.py

Exit codes:
    0  success
    1  fetch or parse failure
    2  database failure
"""

import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# ---- Ensure the project directory is on the path ----
sys.path.insert(0, str(Path(__file__).parent))

from config import LOG_DIR
from scraper import fetch_page, parse_page
from database import (
    compute_daily_flow,
    create_tables,
    get_connection,
    log_scrape,
    upsert_observation,
)
from daily_export import export_today
from generate_viz import generate as generate_viz

# ---------------------------------------------------------------------------
# Logging setup — writes to logs/scraper.log + stdout
# ---------------------------------------------------------------------------
log_path = Path(LOG_DIR)
log_path.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path / "scraper.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def already_have_obs_date(conn, obs_date: str) -> bool:
    """
    Returns True if we already have a successful scrape for this exact obs_date.

    Unlike the old 'already_scraped_today' check, this compares against the
    data date shown on the NWRFC page — not the calendar date we ran.
    Effect: if the page updates later in the day (e.g. page showed Apr 24 data
    at 10 AM but refreshed to Apr 25 data by 2 PM), the next hourly run will
    detect the new obs_date and capture it instead of skipping.
    """
    row = conn.execute("""
        SELECT 1 FROM scrape_log
        WHERE status = 'success' AND obs_date = ?
        LIMIT 1
    """, (obs_date,)).fetchone()
    return row is not None


def _log_error(run_at: str, obs_date, message: str) -> None:
    """Best-effort error log to scrape_log — swallows exceptions."""
    try:
        conn = get_connection()
        create_tables(conn)
        log_scrape(conn, run_at, obs_date, 0, "error", message)
        conn.close()
    except Exception:
        pass


def main() -> int:
    run_at = datetime.now(timezone.utc).isoformat()
    logger.info("=== Daily scrape started ===")

    # 1. Fetch — always, so we can read the obs_date before deciding to skip
    try:
        html = fetch_page()
    except Exception as exc:
        logger.error(f"Fetch failed: {exc}")
        _log_error(run_at, None, f"Fetch failed: {exc}")
        return 1

    # 2. Parse
    try:
        records = parse_page(html)
    except Exception as exc:
        logger.error(f"Parse failed: {exc}")
        _log_error(run_at, None, f"Parse failed: {exc}")
        return 1

    if not records:
        msg = "No records parsed — page structure may have changed"
        logger.warning(msg)
        _log_error(run_at, None, msg)
        return 1

    # Infer obs_date from first record
    obs_date = records[0].get("obs_date") if records else None
    logger.info(f"Page shows obs_date={obs_date} ({len(records)} records parsed)")

    # 3. Skip if we already have this obs_date — page hasn't updated yet
    try:
        conn = get_connection()
        create_tables(conn)
        if already_have_obs_date(conn, obs_date):
            logger.info(f"Already have data for obs_date={obs_date} — page not yet updated, will retry next hour.")
            conn.close()
            return 0
        conn.close()
    except Exception:
        pass  # If check fails, proceed anyway

    # 4. Write to database
    try:
        conn = get_connection()
        create_tables(conn)
        for record in records:
            upsert_observation(conn, record)
        conn.commit()
        log_scrape(conn, run_at, obs_date, len(records), "success")
        # Recompute daily_kaf for the last 3 days so today's row gets populated
        # (yesterday's row may also need a refresh if it was missing a prior day).
        try:
            from datetime import timedelta as _td
            from datetime import date as _date
            today_d = _date.fromisoformat(obs_date.replace("/", "-"))
            n = compute_daily_flow(conn,
                                   start_date=(today_d - _td(days=3)).isoformat(),
                                   end_date=today_d.isoformat())
            logger.info(f"Daily flow updated for last 3 days ({n} RUNOFF rows have daily_kaf)")
        except Exception as exc:
            logger.warning(f"Daily flow compute failed (non-fatal): {exc}")
        conn.close()
        logger.info(f"Upserted {len(records)} records (obs_date={obs_date})")
    except Exception as exc:
        logger.error(f"Database write failed: {exc}")
        return 2

    # 5. Export today's snapshot CSV to daily_results/
    try:
        export_path = export_today(obs_date)
        logger.info(f"Daily export written: {export_path}")
    except Exception as exc:
        logger.warning(f"Daily export failed (non-fatal): {exc}")

    # 6. Regenerate the interactive HTML visualization
    try:
        viz_path = generate_viz()
        logger.info(f"Visualization updated: {viz_path}")
    except Exception as exc:
        logger.warning(f"Visualization failed (non-fatal): {exc}")

    logger.info("=== Daily scrape complete ===")
    return 0


if __name__ == "__main__":
    exit_code = main()
    # Keep the process alive for >10 s so launchd's minimum-runtime guard
    # doesn't mark us as crashed (exit 78) and stop future StartInterval fires.
    # This is only meaningful when called from launchd; interactive runs are
    # unaffected since the sleep happens after all work is done.
    time.sleep(12)
    sys.exit(exit_code)
