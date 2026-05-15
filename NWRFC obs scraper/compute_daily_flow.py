#!/usr/bin/env python3
"""
compute_daily_flow.py
---------------------
Compute the `daily_kaf` column on runoff_observations.

daily_kaf = today.cumul_oct_to_date − yesterday.cumul_oct_to_date
          (only for RUNOFF rows; AVERAGE / PCT_AVG rows stay NULL)

Special cases:
  - Oct 1 (water year start) → daily_kaf = cumul_oct_to_date
  - Gap days (yesterday missing or > 1 day prior) → daily_kaf NULL

Idempotent — safe to re-run any time. Used both as a standalone script
and as a hook from run_daily.py / backfill.py.

Usage:
    /opt/anaconda3/bin/python3 compute_daily_flow.py                     # full table
    /opt/anaconda3/bin/python3 compute_daily_flow.py --start 2026-04-01  # from a date
    /opt/anaconda3/bin/python3 compute_daily_flow.py --recent 7          # last 7 days only
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import compute_daily_flow, ensure_daily_kaf_column, get_connection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   help="End date YYYY-MM-DD")
    parser.add_argument("--recent", type=int,
                        help="Compute only the last N days (overrides --start/--end)")
    args = parser.parse_args()

    if args.recent:
        end_d   = date.today()
        # Include the day before the window so the first day in the window has
        # a prior to diff against.
        start_d = end_d - timedelta(days=args.recent)
        start, end = start_d.isoformat(), end_d.isoformat()
    else:
        start, end = args.start, args.end

    conn = get_connection()
    added = ensure_daily_kaf_column(conn)
    if added:
        print("Added daily_kaf column to runoff_observations.")

    range_str = f"{start or 'beginning'} → {end or 'end'}"
    print(f"Computing daily_kaf for RUNOFF rows: {range_str}")
    n = compute_daily_flow(conn, start, end)
    print(f"Populated daily_kaf for {n:,} RUNOFF rows.")
    conn.close()


if __name__ == "__main__":
    main()
