"""
daily_export.py
---------------
Exports today's most important RUNOFF rows from the database to a CSV in
daily_results/.  Called automatically by run_daily.py after each scrape.

Can also be run standalone to regenerate any date's export:
    python3 daily_export.py                    # latest obs_date in DB
    python3 daily_export.py 2026/04/23         # specific date
"""

import sys
import csv
import sqlite3
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, DAILY_RESULTS_DIR


# Columns included in the daily CSV export (RUNOFF rows only)
EXPORT_COLUMNS = [
    "site_id",
    "site_name",
    "obs_date",
    "water_year",
    "oct", "nov", "dec", "jan", "feb", "mar", "apr",
    "may", "jun", "jul", "aug", "sep",
    "cumul_oct_to_date",
    "cumul_jan_to_date",
    "cumul_apr_to_date",
    # Convenience: pct_avg columns pulled from the PCT_AVG sibling row
    "pct_avg_oct_to_date",
    "pct_avg_jan_to_date",
    "pct_avg_apr_to_date",
]


def export_today(obs_date: str | None = None) -> str:
    """
    Write a CSV for the given obs_date (e.g. '2026/04/23').
    If obs_date is None, uses the most recent date in the database.
    Returns the path to the written file.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Resolve obs_date
    if obs_date is None:
        row = conn.execute(
            "SELECT obs_date FROM runoff_observations ORDER BY obs_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.close()
            raise RuntimeError("No data in database yet — run run_daily.py first")
        obs_date = row["obs_date"]

    # Pull RUNOFF rows for this date
    runoff_rows = conn.execute("""
        SELECT * FROM runoff_observations
        WHERE obs_date = ? AND row_type = 'RUNOFF'
        ORDER BY id
    """, (obs_date,)).fetchall()

    # Pull PCT_AVG rows indexed by site_id for the same date
    pct_rows = {
        r["site_id"]: r for r in conn.execute("""
            SELECT site_id,
                   cumul_oct_to_date AS pct_oct,
                   cumul_jan_to_date AS pct_jan,
                   cumul_apr_to_date AS pct_apr
            FROM runoff_observations
            WHERE obs_date = ? AND row_type = 'PCT_AVG'
        """, (obs_date,)).fetchall()
    }

    conn.close()

    # Build output rows
    output = []
    for r in runoff_rows:
        pct = pct_rows.get(r["site_id"])
        output.append({
            "site_id":              r["site_id"],
            "site_name":            r["site_name"],
            "obs_date":             r["obs_date"],
            "water_year":           r["water_year"],
            "oct":                  r["oct"],
            "nov":                  r["nov"],
            "dec":                  r["dec"],
            "jan":                  r["jan"],
            "feb":                  r["feb"],
            "mar":                  r["mar"],
            "apr":                  r["apr"],
            "may":                  r["may"],
            "jun":                  r["jun"],
            "jul":                  r["jul"],
            "aug":                  r["aug"],
            "sep":                  r["sep"],
            "cumul_oct_to_date":    r["cumul_oct_to_date"],
            "cumul_jan_to_date":    r["cumul_jan_to_date"],
            "cumul_apr_to_date":    r["cumul_apr_to_date"],
            "pct_avg_oct_to_date":  pct["pct_oct"] if pct else None,
            "pct_avg_jan_to_date":  pct["pct_jan"] if pct else None,
            "pct_avg_apr_to_date":  pct["pct_apr"] if pct else None,
        })

    # Write CSV
    os.makedirs(DAILY_RESULTS_DIR, exist_ok=True)
    date_tag = obs_date.replace("/", "-")   # already ISO after migration; belt-and-suspenders
    out_path = os.path.join(DAILY_RESULTS_DIR, f"runoff_{date_tag}.csv")

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(output)

    return out_path


if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else None
    path = export_today(target_date)
    print(f"Exported {path}")
