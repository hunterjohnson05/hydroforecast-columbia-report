#!/usr/bin/env python3
"""
inspect_db.py
-------------
Quick visual inspection of runoff.db. Dumps a few rows for a given site
across all three row types (RUNOFF, AVERAGE, PCT_AVG) so you can see the
data format and compare against the NWRFC website.

Usage:
    /opt/anaconda3/bin/python3 inspect_db.py                       # default: TDAO3W, latest 3 dates → HTML
    /opt/anaconda3/bin/python3 inspect_db.py --site BONO3W
    /opt/anaconda3/bin/python3 inspect_db.py --site TDAO3W --n 5
    /opt/anaconda3/bin/python3 inspect_db.py --site TDAO3W --date 2026-04-23
    /opt/anaconda3/bin/python3 inspect_db.py --format csv          # CSV output instead of HTML
    /opt/anaconda3/bin/python3 inspect_db.py --format terminal     # print to stdout
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "runoff.db"

# Display columns in roughly the same order as the NWRFC page
COLS = [
    "site_id", "obs_date", "row_type",
    "oct", "nov", "dec", "jan", "feb", "mar",
    "apr", "may", "jun", "jul", "aug", "sep",
    "cumul_oct_to_date", "cumul_jan_to_date", "cumul_apr_to_date",
    "daily_kaf",
    "scraped_at",
]


OUT_DIR = Path(__file__).parent.parent / "scripts" / "results" / "db_inspect"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="TDAO3W", help="HB5 site ID (default: TDAO3W)")
    parser.add_argument("--n",    type=int, default=3, help="Number of recent dates to show")
    parser.add_argument("--date", help="Pin to a specific obs_date (YYYY-MM-DD); overrides --n")
    parser.add_argument("--format", choices=["html", "csv", "terminal"], default="html",
                        help="Output format (default: html)")
    parser.add_argument("--out", help="Output file path; default writes to "
                                      "scripts/results/db_inspect/")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))

    # Header info
    name_row = conn.execute(
        "SELECT site_name FROM runoff_observations WHERE site_id = ? LIMIT 1",
        (args.site,),
    ).fetchone()
    if name_row is None:
        print(f"No rows found for site_id = {args.site!r}")
        conn.close()
        return

    site_name = name_row[0]
    total = conn.execute(
        "SELECT COUNT(*), MIN(obs_date), MAX(obs_date) FROM runoff_observations WHERE site_id = ?",
        (args.site,),
    ).fetchone()
    print(f"Site:        {args.site}  ({site_name})")
    print(f"Total rows:  {total[0]:,}")
    print(f"Date range:  {total[1]} → {total[2]}")
    print()

    # Pick which dates to show
    if args.date:
        dates = [args.date]
    else:
        rows = conn.execute("""
            SELECT DISTINCT obs_date FROM runoff_observations
            WHERE site_id = ?
            ORDER BY obs_date DESC LIMIT ?
        """, (args.site, args.n)).fetchall()
        dates = sorted(r[0] for r in rows)

    placeholders = ",".join("?" * len(dates))
    df = pd.read_sql(f"""
        SELECT {", ".join(COLS)}
        FROM runoff_observations
        WHERE site_id = ? AND obs_date IN ({placeholders})
        ORDER BY obs_date, CASE row_type
                              WHEN 'RUNOFF'  THEN 1
                              WHEN 'AVERAGE' THEN 2
                              WHEN 'PCT_AVG' THEN 3
                          END
    """, conn, params=[args.site, *dates])

    if df.empty:
        print(f"No rows for {args.site} on the requested date(s).")
        conn.close()
        return

    # Format dispatch
    if args.format == "terminal":
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 220)
        pd.set_option("display.max_colwidth", 22)
        pd.set_option("display.float_format", lambda v: f"{v:,.1f}" if pd.notna(v) else "")
        for d, sub in df.groupby("obs_date"):
            print(f"=== {d} ===")
            block = sub.drop(columns=["site_id", "obs_date"]).reset_index(drop=True)
            print(block.to_string(index=False))
            print()
    elif args.format == "csv":
        out_path = Path(args.out) if args.out else (
            OUT_DIR / f"{args.site}_{dates[0]}_to_{dates[-1]}.csv"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, float_format="%.2f")
        print(f"Wrote {len(df)} rows: {out_path}")
    else:  # html
        out_path = Path(args.out) if args.out else (
            OUT_DIR / f"{args.site}_{dates[0]}_to_{dates[-1]}.html"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_html(df, args.site, site_name, out_path)
        print(f"Wrote {len(df)} rows: {out_path}")

    conn.close()


def write_html(df: pd.DataFrame, site_id: str, site_name: str, out_path: Path) -> None:
    """Render a self-contained HTML table grouped by obs_date, NWRFC-page-style."""
    blocks = []
    for d, sub in df.groupby("obs_date"):
        block = sub.drop(columns=["site_id", "obs_date"]).copy()
        # Format floats with commas + one decimal; blank for NaN
        for col in block.columns:
            if pd.api.types.is_numeric_dtype(block[col]):
                block[col] = block[col].map(lambda v: f"{v:,.1f}" if pd.notna(v) else "")
        blocks.append(
            f"<h3>{d}</h3>\n"
            + block.to_html(index=False, classes="data", border=0, na_rep="")
        )

    today = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{site_id} — DB Inspection</title>
<style>
  body {{ font-family: -apple-system, sans-serif; padding: 24px;
         color: #222; background: #f7f7f7; max-width: 1400px; margin: 0 auto; }}
  header {{ border-bottom: 1px solid #ddd; padding-bottom: 12px; margin-bottom: 24px; }}
  header h1 {{ font-size: 1.2rem; margin-bottom: 4px; }}
  header p  {{ font-size: 0.82rem; color: #666; }}
  h3 {{ font-size: 0.95rem; margin: 20px 0 6px; color: #333; }}
  table.data {{ width: 100%; border-collapse: collapse; background: white;
                box-shadow: 0 1px 2px rgba(0,0,0,0.06); font-size: 0.78rem; }}
  table.data th, table.data td {{ padding: 6px 8px; text-align: right;
                                  border-bottom: 1px solid #eee; }}
  table.data th {{ background: #f0f0f0; font-weight: 600; text-align: left; }}
  table.data tr td:first-child, table.data tr th:first-child {{ text-align: left; font-weight: 600; }}
  table.data tr:hover {{ background: #fafafa; }}
</style>
</head>
<body>
<header>
  <h1>{site_id} — {site_name}</h1>
  <p>DB inspection from runoff.db &nbsp;·&nbsp; Generated {today}</p>
</header>
{"".join(blocks)}
</body>
</html>"""
    out_path.write_text(html)


if __name__ == "__main__":
    main()
