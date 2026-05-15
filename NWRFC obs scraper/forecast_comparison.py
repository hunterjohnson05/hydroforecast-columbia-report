#!/usr/bin/env python3
"""
forecast_comparison.py
-----------------------
Pulls HydroForecast (ERA5 mean) and RFC (nwrfc-esp-natural mean) forecasts
from the Upstream Tech API, compares against NWRFC observed cumulative
Apr-to-date volumes from the local DB, and writes a self-contained HTML
report with a styled table + Plotly accumulated-volume chart.

Usage:
    python3 forecast_comparison.py
    python3 forecast_comparison.py --start 2026-04-01 --end 2026-04-28

To add more sites: append entries to SITES below.
"""

import os
import sys
import json
import sqlite3
import argparse
import requests
import pandas as pd
from pathlib import Path
from datetime import date, timedelta, datetime

# ── Paths / config ────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent

# Load .env if present
_env = PROJECT_DIR / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("UPSTREAM_API_KEY", "")
API_URL = "https://api.upstream.tech/api/v2/timeseries/forecasts"
DB_PATH = str(PROJECT_DIR / "runoff.db")
OUT_DIR = PROJECT_DIR.parent / "scripts" / "results" / "forecast_comparison"

CFS_TO_TAF = 1.9835 / 1000   # 1 CFS flowing for 1 day = 1.9835 AF


# ── Site configuration ────────────────────────────────────────────────────────
# Add more sites here; each will get its own table + chart section in the output.
SITES = [
    {
        "label":       "The Dalles",
        "api_site_id": "shared_regional-the-dalles",
        "db_site_id":  "TDAO3W",   # natural/unregulated — matches nwrfc-esp-natural
        "project_id":  "shared_regional-pacific-northwest",
    },
    # Example — uncomment and fill in to add another site:
    # {
    #     "label":       "Bonneville Dam",
    #     "api_site_id": "shared_regional-bonneville",
    #     "db_site_id":  "BONO3W",
    #     "project_id":  "shared_regional-pacific-northwest",
    # },
]


# ── API helpers ───────────────────────────────────────────────────────────────
def call_api(api_site_id: str, project_id: str, init_date_iso: str) -> dict:
    """
    POST to the HydroForecast API requesting RFC and HF ERA5 daily rate
    forecasts initialized on init_date_iso (ISO 8601 UTC string).
    Returns the raw JSON response dict.
    """
    body = {
        "queries": [
            {
                "source": "nwrfc-esp-natural",
                "columns": ["discharge_mean"],
                "siteId": api_site_id,
                "timeAggregation": "1D",
                "rateVolumeMode": "rate",
                "projectId": project_id,
                "unitSystem": "US",
                "initializationTimes": [init_date_iso],
            },
            {
                # hydroforecast-seasonal is the blended ERA5+GEFS product that
                # matches the "HydroForecast Mean" shown in the HF dashboard.
                # Do NOT use hydroforecast-seasonal-3-era5 alone — it gives a
                # significantly lower value than the combined product.
                "source": "hydroforecast-seasonal",
                "columns": ["discharge_mean"],
                "sourceMetadata": {"modelGeneration": "Seasonal-3"},
                "forecastLengthDays": 365,
                "siteId": api_site_id,
                "timeAggregation": "1D",
                "rateVolumeMode": "rate",
                "projectId": project_id,
                "unitSystem": "US",
                "initializationTimes": [init_date_iso],
            },
        ]
    }
    r = requests.post(API_URL, headers={"Authorization": API_KEY}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_daily_cfs(result: dict) -> dict[str, float]:
    """
    Parse one query result from the API response into a
    {date_str: daily_mean_CFS} dict.
    """
    forecasts = result.get("forecasts", [])
    if not forecasts:
        return {}
    fc = forecasts[0]
    valid_times = fc.get("validTimes", [])
    means = fc.get("data", {}).get("discharge_mean", [])
    daily = {}
    for t, v in zip(valid_times, means):
        if v is None:
            continue
        d = t[:10]   # "YYYY-MM-DD"
        daily[d] = v
    return daily


def accumulate(daily_cfs: dict[str, float],
               start: date, end: date,
               init_hour_utc: int = 0) -> tuple[list[str], list[float]]:
    """
    Accumulate daily CFS flows → running TAF total from start through end.
    If init_hour_utc > 0, the first day (start) is weighted as a partial day
    covering only the hours from init_hour_utc through midnight (matching how
    the HF dashboard accumulates from initialization time, not midnight).
    Returns (all_dates, all_cumul_taf).
    """
    dates, cumul = [], []
    running = 0.0
    d = start
    first = True
    while d <= end:
        ds = d.isoformat()
        if ds in daily_cfs:
            weight = (24 - init_hour_utc) / 24 if (first and init_hour_utc > 0) else 1.0
            running += daily_cfs[ds] * CFS_TO_TAF * weight
        dates.append(ds)
        cumul.append(round(running, 1))
        d += timedelta(days=1)
        first = False
    return dates, cumul


def weekly_sample(dates: list[str], cumul: list[float]) -> tuple[list[str], list[float]]:
    """Return every-7th-day sample plus the final point."""
    idx = list(range(0, len(dates), 7))
    if idx[-1] != len(dates) - 1:
        idx.append(len(dates) - 1)
    return [dates[i] for i in idx], [cumul[i] for i in idx]


# ── DB helpers ────────────────────────────────────────────────────────────────
def get_observed_series(db_site_id: str, start: date, end: date) -> tuple[list[str], list[float]]:
    """
    Pull daily cumul_apr_to_date (KAF = TAF) from NWRFC DB for the given
    site and date range.  Returns (all_dates, all_cumul_taf).
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT obs_date, cumul_apr_to_date
        FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF'
          AND obs_date >= ? AND obs_date <= ?
        ORDER BY obs_date
    """, (db_site_id, start.isoformat(), end.isoformat())).fetchall()
    conn.close()
    if not rows:
        return [], []
    return [r[0] for r in rows], [r[1] for r in rows]


# ── HTML builders ─────────────────────────────────────────────────────────────
def fmt_err(v: float) -> str:
    return f"+{v:,.0f} TAF" if v >= 0 else f"{v:,.0f} TAF"

def fmt_pct(v: float) -> str:
    return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"


def build_table_html(label: str, snap_date: str, period_start: date,
                     observed: float, hf: float, rfc: float,
                     created_on: str = "") -> str:
    """Single-snapshot table matching the reference format."""
    hf_err  = hf  - observed
    rfc_err = rfc - observed
    hf_pct  = hf_err  / observed * 100 if observed else 0
    rfc_pct = rfc_err / observed * 100 if observed else 0

    start_label = period_start.strftime("%b %-d, %Y")
    d_label     = date.fromisoformat(snap_date).strftime("%b %-d, %Y")
    created_str = f" &nbsp;·&nbsp; Created {created_on}" if created_on else ""

    return f"""
<div class="site-block">
  <h2>{label} — Seasonal Volume Forecast Comparison
      &nbsp;<em>Observed (NWRFC): {observed:,.0f} TAF</em></h2>
  <p class="period-label">{start_label} – {d_label} &nbsp;·&nbsp; {start_label} initialization{created_str}</p>
  <table>
    <thead>
      <tr>
        <th>Forecast</th>
        <th>Value (TAF)</th>
        <th>Error (TAF)</th>
        <th>% Error</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>HF</td>
        <td>{hf:,.0f} TAF</td>
        <td>{fmt_err(hf_err)}</td>
        <td>{fmt_pct(hf_pct)}</td>
      </tr>
      <tr>
        <td>RFC</td>
        <td>{rfc:,.0f} TAF</td>
        <td>{fmt_err(rfc_err)}</td>
        <td>{fmt_pct(rfc_pct)}</td>
      </tr>
    </tbody>
  </table>
</div>"""


def build_bar_chart_json(label: str, snap_rows: list, period_start: date) -> str:
    """
    Grouped bar chart: one group per snapshot date, three bars each
    (Observed, HF, RFC).  snap_rows is a list of dicts: {date, obs, hf, rfc}.
    """
    start_mo = period_start.strftime("%b %-d")
    dates    = [r["date"] for r in snap_rows]
    obs_vals = [round(r["obs"], 0) for r in snap_rows]
    hf_vals  = [round(r["hf"],  0) for r in snap_rows]
    rfc_vals = [round(r["rfc"], 0) for r in snap_rows]

    traces = [
        {
            "type": "bar", "name": "Observed (NWRFC)",
            "x": dates, "y": obs_vals,
            "marker": {"color": "#1a3a5c"},
            "text": [f"{v:,.0f}" for v in obs_vals],
            "textposition": "outside",
            "hovertemplate": "<b>Observed</b><br>%{x}<br>%{y:,.0f} TAF<extra></extra>",
        },
        {
            "type": "bar", "name": "HF (seasonal mean)",
            "x": dates, "y": hf_vals,
            "marker": {"color": "#2ca02c"},
            "text": [f"{v:,.0f}" for v in hf_vals],
            "textposition": "outside",
            "hovertemplate": "<b>HF</b><br>%{x}<br>%{y:,.0f} TAF<extra></extra>",
        },
        {
            "type": "bar", "name": "RFC (ESP mean)",
            "x": dates, "y": rfc_vals,
            "marker": {"color": "#d62728"},
            "text": [f"{v:,.0f}" for v in rfc_vals],
            "textposition": "outside",
            "hovertemplate": "<b>RFC</b><br>%{x}<br>%{y:,.0f} TAF<extra></extra>",
        },
    ]
    layout = {
        "title": f"{label} — Cumulative {start_mo}-to-Date Volume by Snapshot",
        "barmode": "group",
        "xaxis": {"title": "Snapshot Date", "gridcolor": "#eee", "type": "category"},
        "yaxis": {"title": "Cumulative Volume (TAF)", "gridcolor": "#eee"},
        "plot_bgcolor": "white",
        "paper_bgcolor": "white",
        "legend": {"orientation": "h", "y": -0.25},
        "height": 420,
        "margin": {"t": 50, "r": 20, "b": 100, "l": 80},
    }
    return json.dumps({"traces": traces, "layout": layout})


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=date(date.today().year, 4, 1).isoformat(),
                        help="Period start date (YYYY-MM-DD), default Apr 1 of current year")
    parser.add_argument("--end",   default=None,
                        help="Period end date (YYYY-MM-DD), default latest DB obs")
    args = parser.parse_args()

    period_start = date.fromisoformat(args.start)

    # Resolve period end: use latest obs date in DB if not specified
    if args.end:
        period_end = date.fromisoformat(args.end)
    else:
        conn = sqlite3.connect(DB_PATH)
        latest = conn.execute("SELECT MAX(obs_date) FROM runoff_observations").fetchone()[0]
        conn.close()
        period_end = date.fromisoformat(latest)

    # The API canonical init is Apr 1 00:00 UTC (displayed as Mar 31 17:00 PDT in the dashboard).
    # The API returns data starting from Mar 31, and the dashboard accumulates from
    # Mar 31 17:00 UTC — i.e. 7/24 of Mar 31's daily flow. We replicate that here.
    INIT_HOUR_UTC = 17                               # hours into Mar 31 when init occurred
    accum_start   = period_start - timedelta(days=1) # Mar 31 — first day returned by API
    init_iso      = f"{period_start.isoformat()}T00:00:00.000Z"  # canonical API init time
    period_label = f"Apr 1 – {period_end.strftime('%b %-d, %Y')} (Apr-to-date)"
    generated    = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"Period: {period_start} → {period_end}")
    print(f"Initialization: {init_iso}")

    site_blocks   = []   # HTML table sections
    chart_scripts = []   # inline JS Plotly calls

    for i, site in enumerate(SITES):
        label       = site["label"]
        api_site_id = site["api_site_id"]
        db_site_id  = site["db_site_id"]
        project_id  = site["project_id"]
        div_id      = f"chart-{i}"

        print(f"\nProcessing: {label}")

        # ── Single API call covering the full period (Apr 1 init) ──────────────
        # We read cumulative at multiple snapshot dates from the same forecast run.
        print(f"  Calling API (init={init_iso}) …")
        resp = call_api(api_site_id, project_id, init_iso)
        results = resp.get("data", [])
        if len(results) < 2:
            print(f"  WARNING: API returned fewer than 2 results — skipping")
            continue

        # results[0] = nwrfc-esp-natural (RFC), results[1] = hydroforecast-seasonal (HF)
        rfc_daily = parse_daily_cfs(results[0])
        hf_daily  = parse_daily_cfs(results[1])

        # ── Snapshot dates: every obs date in DB from period_start to period_end ─
        obs_dates_all, obs_cumul_all = get_observed_series(db_site_id, period_start, period_end)
        if not obs_dates_all:
            print(f"  WARNING: no observed data for {db_site_id} — skipping")
            continue

        # Build snapshots: one row per available obs date
        # Weekly cadence: take every 7th obs date plus the final one
        snap_indices = list(range(0, len(obs_dates_all), 7))
        if snap_indices[-1] != len(obs_dates_all) - 1:
            snap_indices.append(len(obs_dates_all) - 1)

        snap_rows = []   # list of dicts: {date, obs, hf, rfc}
        for idx in snap_indices:
            snap_date = date.fromisoformat(obs_dates_all[idx])
            obs_val   = obs_cumul_all[idx]
            if obs_val is None:
                # DB row has NULL cumul_apr_to_date for this date — skip the snapshot
                # rather than crashing on f-string formatting.
                print(f"  {obs_dates_all[idx]}: obs=NULL — skipping snapshot")
                continue

            _, hf_cumul  = accumulate(hf_daily,  accum_start, snap_date, INIT_HOUR_UTC)
            _, rfc_cumul = accumulate(rfc_daily, accum_start, snap_date, INIT_HOUR_UTC)
            hf_val  = hf_cumul[-1]  if hf_cumul  else 0.0
            rfc_val = rfc_cumul[-1] if rfc_cumul else 0.0

            snap_rows.append({
                "date": obs_dates_all[idx],
                "obs":  obs_val,
                "hf":   hf_val,
                "rfc":  rfc_val,
            })
            print(f"  {obs_dates_all[idx]}: obs={obs_val:,.0f}  hf={hf_val:,.0f}  rfc={rfc_val:,.0f} TAF")

        # ── One table per snapshot date ──
        for row in snap_rows:
            site_blocks.append(build_table_html(
                label, row["date"], period_start, row["obs"], row["hf"], row["rfc"],
                created_on=generated,
            ))
        site_blocks.append(f'<div id="{div_id}" class="chart-wrap"></div>')

        # ── Grouped bar chart (all snapshots) ──
        chart_data = build_bar_chart_json(label, snap_rows, period_start)
        chart_scripts.append(f"""
(function() {{
  var d = {chart_data};
  Plotly.newPlot("{div_id}", d.traces, d.layout, {{responsive: true}});
}})();""")

    # ── Assemble HTML ──────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "forecast_comparison.html"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HydroForecast vs NWRFC Observed — Comparison</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Georgia, serif;
      background: #f7f7f7; color: #222; padding: 32px;
    }}
    header {{ margin-bottom: 32px; }}
    header h1 {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 4px; }}
    header p  {{ font-size: 0.82rem; color: #666; }}
    .site-block {{ background: white; border-radius: 6px;
                  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
                  padding: 28px 32px; margin-bottom: 32px; }}
    .site-block h2 {{
      font-size: 1.05rem; font-weight: 700;
      border-bottom: 1px solid #e0e0e0; padding-bottom: 10px; margin-bottom: 6px;
    }}
    .site-block h2 em {{ font-weight: 400; font-style: italic; color: #555; }}
    .period-label {{ font-size: 0.8rem; color: #888; margin-bottom: 14px; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
    thead tr {{ background: #f0f0f0; }}
    th {{ text-align: left; padding: 9px 14px; font-size: 0.88rem;
          font-weight: 600; border-bottom: 2px solid #ccc; }}
    td {{ padding: 10px 14px; font-size: 0.9rem;
          border-bottom: 1px solid #ebebeb; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .chart-wrap {{ margin-top: 8px; }}
  </style>
</head>
<body>
<header>
  <h1>HydroForecast vs RFC vs NWRFC Observed — Volume Comparison</h1>
  <p>Generated {generated} &nbsp;·&nbsp; Period: {period_start} – {period_end} ({period_start.strftime("%b %-d")}-to-date)
     &nbsp;·&nbsp; HF source: hydroforecast-seasonal mean (ERA5+GEFS blended) &nbsp;·&nbsp; RFC source: NWRFC ESP natural mean</p>
</header>

{"".join(site_blocks)}

<script>
{"".join(chart_scripts)}
</script>
</body>
</html>"""

    out_path.write_text(html)
    print(f"\nOutput written: {out_path}")
    return str(out_path)


if __name__ == "__main__":
    main()
