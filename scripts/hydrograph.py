#!/usr/bin/env python3
"""
hydrograph.py
-------------
Interactive Plotly hydrograph of daily mean discharge:

  - Past 4 HF forecasts (today, T-10d, T-20d, T-30d): mean + 50% CI + 90% CI
  - Past 4 NWRFC ESP forecasts at the same inits: mean only (dashed)
  - Historical daily mean (LTA): from `historical-percentile-daily-gauge-observation`
    via the `/timeseries/observations` endpoint
  - Observed daily flow: from local `runoff.db` (daily_kaf → mean CFS)
  - Vertical "Now" marker at today's date

Legend-toggleable: click any forecast entry in the legend to show/hide its
mean + CI traces as a unit (via Plotly `legendgroup`). By default only today's
forecast is visible; older inits are `legendonly` so the chart isn't cluttered
on first load.

Outputs two files per site:
  - <site>_hydrograph_<date>.html         — standalone, plotly.js from CDN
  - <site>_hydrograph_<date>_snippet.html — embeddable in another HTML (no plotly.js)

Adapted from columbia river/macquarie_pnw.py (matplotlib version shared with the
Macquarie client). The "drop NaN/0 mean" cleaning step and the color scheme
(skyblue/steelblue/navy + dotted LTA) come from that script.
"""

import argparse
import os
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
import requests

API_BASE = "https://api.upstream.tech/api/v2"

# KAF/day → mean CFS for that day. (1 CFS·day = 86400/43560 = 1.9835 AF → 1 KAF/day = 1000/1.9835 CFS)
KAF_PER_DAY_TO_CFS = 1000 / 1.9835

PROJECT_DIR = Path(__file__).parent
RESULTS_DIR = PROJECT_DIR / "results" / "hydrograph"
DB_PATH     = PROJECT_DIR.parent / "NWRFC obs scraper" / "runoff.db"

# Forecast inits — hybrid cadence:
#   - Calendar anchors: anchor date (earliest API availability) + 1st & 15th
#     of every month from anchor up through today.
#   - Rolling window: today, today−10d, today−20d, today−30d.
#   - Dedupe: drop any rolling date within ROLLING_DEDUPE_DAYS of a calendar
#     anchor (so e.g. T-30 = Apr 14 is dropped because Apr 15 is the calendar
#     anchor for that slot).
# This gives dense recent skill (10-day rolling) and clean monthly anchors
# deeper in history. The list grows naturally as the season progresses.
OPERATIONAL_FORECAST_START = date(2026, 2, 26)   # earliest HF init in the API for TDAO3W
ROLLING_LOOKBACK_DAYS      = 30
ROLLING_STEP_DAYS          = 10
ROLLING_DEDUPE_DAYS        = 1                   # rolling-vs-calendar overlap threshold
CALENDAR_DAYS_OF_MONTH     = (1, 15)             # which days of the month to anchor on

FORECAST_HORIZON_DAYS      = 365
HISTORICAL_WINDOW_DAYS     = 90                  # ~3 months of observed before today
DEFAULT_FORWARD_VIEW_DAYS  = 180                 # initial x-axis range extends ~6 months ahead


def generate_init_dates(today_d: date, anchor_d: date) -> list[date]:
    """
    Build the forecast-init schedule: calendar anchors + rolling window, deduped.
    Returns a chronologically sorted list of dates in [anchor_d, today_d].
    """
    # Calendar anchors: anchor itself + 1st/15th of each month from anchor → today
    calendar: set[date] = {anchor_d, today_d}
    cur = date(anchor_d.year, anchor_d.month, 1)
    while cur <= today_d:
        for day in CALENDAR_DAYS_OF_MONTH:
            d = date(cur.year, cur.month, day)
            if anchor_d <= d <= today_d:
                calendar.add(d)
        # advance one calendar month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    # Rolling window (latest first): today, today-step, today-2*step, … up to lookback.
    rolling: set[date] = set()
    o = 0
    while o <= ROLLING_LOOKBACK_DAYS:
        d = today_d - timedelta(days=o)
        if d >= anchor_d:
            rolling.add(d)
        o += ROLLING_STEP_DAYS

    # Combine, dropping any rolling date within ROLLING_DEDUPE_DAYS of a calendar anchor.
    result = set(calendar)
    for d in rolling:
        if any(abs((d - c).days) <= ROLLING_DEDUPE_DAYS for c in calendar):
            continue
        result.add(d)

    return sorted(result)

# Color generation: sample Plotly's Blues / Oranges colorscales for HF / RFC,
# newest → oldest = dark → light. Positions chosen to skip the very darkest
# (often near-black) and very lightest (often near-white) ends of each scale.
def make_color_scheme(n: int, scale_name: str) -> list[str]:
    if n <= 0:
        return []
    if n == 1:
        return pc.sample_colorscale(scale_name, [0.85])
    positions = [0.95 - 0.70 * i / (n - 1) for i in range(n)]
    return pc.sample_colorscale(scale_name, positions)

# Per-site config (add a new HB5 ID + API site id to extend coverage).
# Optional per-site key: `forecast_start` = date of the earliest HF init the API
# has for that site. Defaults to OPERATIONAL_FORECAST_START. Run a daily probe
# (see commit notes / memory) to find a site's exact value if it differs.
SITES = {
    "TDAO3W": {
        "label":       "The Dalles",
        "api_site_id": "shared_regional-the-dalles",
        "project_id":  "shared_regional-pacific-northwest",
        "forecast_start": date(2026, 2, 26),
    },
    # Future sites — uncomment & probe API for each site's actual earliest HF date:
    # "WBIQ1W": {
    #     "label":          "Whitebird",
    #     "api_site_id":    "shared_regional-whitebird",
    #     "project_id":     "shared_regional-pacific-northwest",
    #     "forecast_start": date(2026, 2, 26),  # confirm via API probe
    # },
}


# ── API helpers ───────────────────────────────────────────────────────────────
def fetch_forecasts(api_key: str, site_id: str, project_id: str,
                     source: str, init_times: list[str],
                     source_metadata: dict | None = None) -> list[dict]:
    """Batch-fetch forecasts for the given init times (one POST)."""
    query: dict = {
        "source": source,
        "columns": ["discharge_mean",
                     "discharge_q0.05", "discharge_q0.25",
                     "discharge_q0.75", "discharge_q0.95"],
        "siteId": site_id,
        "timeAggregation": "1D",
        "rateVolumeMode": "rate",
        "projectId": project_id,
        "unitSystem": "US",
        "initializationTimes": init_times,
        "forecastLengthDays": FORECAST_HORIZON_DAYS,
    }
    if source_metadata:
        query["sourceMetadata"] = source_metadata
    resp = requests.post(f"{API_BASE}/timeseries/forecasts",
                          json={"queries": [query]},
                          headers={"Authorization": api_key},
                          timeout=180)
    resp.raise_for_status()
    return resp.json()["data"][0]["forecasts"]


def fetch_lta(api_key: str, site_id: str, project_id: str,
               start_d: date, end_d: date) -> dict:
    """Daily climatology (long-term-average daily mean) from /observations."""
    query: dict = {
        "source":          "historical-percentile-daily-gauge-observation",
        "columns":         ["flowDailyMean"],
        "siteId":          site_id,
        "projectId":       project_id,
        "timeAggregation": "1D",
        "rateVolumeMode":  "rate",
        "unitSystem":      "US",
        "startDate":       f"{start_d.isoformat()}T00:00:00.000Z",
        "endDate":         f"{end_d.isoformat()}T00:00:00.000Z",
    }
    resp = requests.post(f"{API_BASE}/timeseries/observations",
                          json={"queries": [query]},
                          headers={"Authorization": api_key},
                          timeout=120)
    resp.raise_for_status()
    return resp.json()["data"][0]


def fetch_observed(db_site_id: str, start_d: date, end_d: date) -> pd.DataFrame:
    """Observed daily flow from local DB → DataFrame indexed by date with 'cfs' column."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT obs_date, daily_kaf FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF'
              AND obs_date >= ? AND obs_date <= ?
              AND daily_kaf IS NOT NULL
        ORDER BY obs_date
    """, (db_site_id, start_d.isoformat(), end_d.isoformat())).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame(columns=["cfs"])
    df = pd.DataFrame(rows, columns=["date", "daily_kaf"])
    df["date"] = pd.to_datetime(df["date"])
    df["cfs"] = df["daily_kaf"] * KAF_PER_DAY_TO_CFS
    return df.set_index("date")[["cfs"]]


# ── DataFrame helpers ─────────────────────────────────────────────────────────
def parse_forecast(fc: dict) -> pd.DataFrame:
    """One forecast → daily-indexed DataFrame with mean/q05/q25/q75/q95 columns.

    Drops rows where the mean is NaN or 0 to suppress dip-to-zero artifacts at
    forecast edges (technique from the Macquarie script)."""
    valid_times = pd.to_datetime(fc["validTimes"], utc=True)
    data = fc.get("data", {})
    keys_map = {
        "mean": "discharge_mean",
        "q05":  "discharge_q0.05",
        "q25":  "discharge_q0.25",
        "q75":  "discharge_q0.75",
        "q95":  "discharge_q0.95",
    }
    df = pd.DataFrame({"time": valid_times})
    for col, api_key in keys_map.items():
        if api_key in data:
            df[col] = data[api_key]
    if "mean" not in df.columns:
        return df.iloc[0:0]
    df = df[(df["mean"].notna()) & (df["mean"] != 0)]
    return df.set_index("time").resample("D").mean().dropna(how="all")


def init_date_of(fc: dict) -> date:
    d = pd.to_datetime(fc["initializationTime"])
    if d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    return d.date()


def to_rgba(color: str, alpha: float) -> str:
    """Accept `#rrggbb` or `rgb(r,g,b)` (Plotly colorscale output) → `rgba(r,g,b,alpha)`."""
    if color.startswith("#"):
        h = color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    elif color.startswith("rgb"):
        import re
        m = re.search(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", color)
        if not m:
            raise ValueError(f"Unparseable color: {color!r}")
        r, g, b = int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3)))
    else:
        raise ValueError(f"Unsupported color format: {color!r}")
    return f"rgba({r},{g},{b},{alpha})"


# ── Figure ────────────────────────────────────────────────────────────────────
def build_figure(hf_forecasts: list[dict],
                  rfc_forecasts: list[dict],
                  lta_data: dict,
                  obs_df: pd.DataFrame,
                  site_label: str,
                  today_d: date,
                  chart_start: date,
                  hf_colors: list[str],
                  rfc_colors: list[str]) -> go.Figure:
    fig = go.Figure()

    # Pair HF and RFC forecasts by init date (newest first).
    # Use intersection so we don't render orphan single-model forecasts (e.g. the
    # Feb 26 anchor occasionally has RFC but no HF — drop it rather than showing
    # a lone dashed RFC line).
    hf_by_init  = {init_date_of(fc): fc for fc in hf_forecasts}
    rfc_by_init = {init_date_of(fc): fc for fc in rfc_forecasts}
    paired_inits = sorted(set(hf_by_init) & set(rfc_by_init), reverse=True)

    for i, init_d in enumerate(paired_inits):
        days_ago = (today_d - init_d).days
        label = init_d.strftime("%b %-d")           # e.g. "May 14"
        legendgroup = f"forecast-{init_d.isoformat()}"
        # Only today's forecast is visible by default; older are `legendonly`
        visible = True if days_ago == 0 else "legendonly"

        hf_color  = hf_colors[min(i, len(hf_colors) - 1)]
        rfc_color = rfc_colors[min(i, len(rfc_colors) - 1)]

        # ── HF forecast ──────────────────────────────────────────────────────
        if init_d in hf_by_init:
            df_hf = parse_forecast(hf_by_init[init_d])
            if not df_hf.empty:
                # 90% CI band (q05 → q95). Two traces: invisible lower line, then
                # fill="tonexty" on the upper line creates the shaded band.
                if "q05" in df_hf.columns and "q95" in df_hf.columns:
                    fig.add_trace(go.Scatter(
                        x=df_hf.index, y=df_hf["q05"],
                        mode="lines", line={"width": 0},
                        legendgroup=legendgroup, showlegend=False,
                        visible=visible, hoverinfo="skip",
                        name=f"HF q05 ({label})",
                    ))
                    fig.add_trace(go.Scatter(
                        x=df_hf.index, y=df_hf["q95"],
                        mode="lines", line={"width": 0},
                        fill="tonexty", fillcolor=to_rgba(hf_color, 0.15),
                        legendgroup=legendgroup, showlegend=False,
                        visible=visible, hoverinfo="skip",
                        name=f"HF 90% CI ({label})",
                    ))
                # 50% CI band (q25 → q75)
                if "q25" in df_hf.columns and "q75" in df_hf.columns:
                    fig.add_trace(go.Scatter(
                        x=df_hf.index, y=df_hf["q25"],
                        mode="lines", line={"width": 0},
                        legendgroup=legendgroup, showlegend=False,
                        visible=visible, hoverinfo="skip",
                        name=f"HF q25 ({label})",
                    ))
                    fig.add_trace(go.Scatter(
                        x=df_hf.index, y=df_hf["q75"],
                        mode="lines", line={"width": 0},
                        fill="tonexty", fillcolor=to_rgba(hf_color, 0.30),
                        legendgroup=legendgroup, showlegend=False,
                        visible=visible, hoverinfo="skip",
                        name=f"HF 50% CI ({label})",
                    ))
                # Mean line — the only HF trace that shows in the legend for this init
                fig.add_trace(go.Scatter(
                    x=df_hf.index, y=df_hf["mean"],
                    mode="lines",
                    line={"color": hf_color, "width": 2.2},
                    legendgroup=legendgroup, showlegend=True,
                    visible=visible,
                    name=f"HF Mean ({label})",
                    hovertemplate=("HF " + label
                                    + "<br>%{x|%b %d, %Y}: %{y:,.0f} cfs<extra></extra>"),
                ))

        # ── NWRFC ESP forecast (mean only, dashed) ──────────────────────────
        if init_d in rfc_by_init:
            df_rfc = parse_forecast(rfc_by_init[init_d])
            if not df_rfc.empty and "mean" in df_rfc.columns:
                fig.add_trace(go.Scatter(
                    x=df_rfc.index, y=df_rfc["mean"],
                    mode="lines",
                    line={"color": rfc_color, "width": 1.7, "dash": "dash"},
                    legendgroup=legendgroup, showlegend=True,
                    visible=visible,
                    name=f"NWRFC ESP ({label})",
                    hovertemplate=("RFC " + label
                                    + "<br>%{x|%b %d, %Y}: %{y:,.0f} cfs<extra></extra>"),
                ))

    # ── Historical LTA (always visible by default) ──────────────────────────
    if lta_data and "timestamps" in lta_data and "data" in lta_data:
        df_lta = pd.DataFrame({
            "time": pd.to_datetime(lta_data["timestamps"], utc=True),
            "mean": lta_data["data"].get("flowDailyMean", []),
        })
        df_lta = df_lta[(df_lta["mean"].notna()) & (df_lta["mean"] != 0)]
        if not df_lta.empty:
            fig.add_trace(go.Scatter(
                x=df_lta["time"], y=df_lta["mean"],
                mode="lines",
                line={"color": "black", "width": 1.8, "dash": "dot"},
                name="Historical Mean (LTA)",
                hovertemplate="LTA<br>%{x|%b %d, %Y}: %{y:,.0f} cfs<extra></extra>",
            ))

    # ── Observed daily flow (always visible by default) ──────────────────────
    if not obs_df.empty:
        fig.add_trace(go.Scatter(
            x=obs_df.index, y=obs_df["cfs"],
            mode="lines",
            line={"color": "#222", "width": 2.5},
            name="Observed (local DB)",
            hovertemplate="Observed<br>%{x|%b %d, %Y}: %{y:,.0f} cfs<extra></extra>",
        ))

    # ── "Now" vertical line ──────────────────────────────────────────────────
    # NOTE: `fig.add_vline(... annotation_text=...)` errors on recent pandas
    # because Plotly internally averages two Timestamps. Use add_shape + a
    # separate annotation instead, which sidesteps the arithmetic.
    today_ts = pd.Timestamp(today_d)
    fig.add_shape(
        type="line",
        xref="x", yref="paper",
        x0=today_ts, x1=today_ts,
        y0=0, y1=1,
        line={"color": "gray", "width": 1, "dash": "dot"},
        opacity=0.6,
    )
    fig.add_annotation(
        x=today_ts, y=1.0, xref="x", yref="paper",
        text="Now", showarrow=False,
        xanchor="left", yanchor="bottom",
        font={"color": "gray", "size": 11},
        xshift=4, yshift=2,
    )

    # ── Layout ───────────────────────────────────────────────────────────────
    fig.update_layout(
        title=f"{site_label} — Daily Hydrograph",
        xaxis_title="Date",
        yaxis_title="Discharge (cfs)",
        yaxis_tickformat=",",
        hovermode="x unified",
        legend={"orientation": "v", "x": 1.02, "y": 1},
        margin={"r": 220, "t": 60, "b": 50, "l": 70},
        height=620,
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#eee", showline=True, linecolor="#999",
                     range=[chart_start, today_d + timedelta(days=DEFAULT_FORWARD_VIEW_DAYS)])
    fig.update_yaxes(showgrid=True, gridcolor="#eee", showline=True, linecolor="#999")

    return fig


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="TDAO3W", choices=list(SITES.keys()))
    parser.add_argument("--api-key", default=os.environ.get("HF_API_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API key required (HF_API_KEY env var or --api-key)")

    cfg = SITES[args.site]
    today_d = date.today()

    # Hybrid cadence: calendar anchors (anchor + 1st/15th of each month) + rolling
    # (today, T-10, T-20, T-30). See generate_init_dates() for the dedupe rule.
    forecast_start = cfg.get("forecast_start", OPERATIONAL_FORECAST_START)
    init_dates = generate_init_dates(today_d, forecast_start)
    init_times = [
        datetime(d.year, d.month, d.day, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for d in init_dates
    ]

    # Sample colorscales for N inits — newest dark, oldest light.
    hf_colors  = make_color_scheme(len(init_dates), "Blues")
    rfc_colors = make_color_scheme(len(init_dates), "Oranges")

    chart_start = today_d - timedelta(days=HISTORICAL_WINDOW_DAYS)
    chart_end   = today_d + timedelta(days=FORECAST_HORIZON_DAYS)

    print(f"Site: {args.site} ({cfg['label']})")
    print(f"Operational forecast start: {OPERATIONAL_FORECAST_START}")
    print(f"Forecast inits ({len(init_dates)}): {[d.isoformat() for d in init_dates]}")
    print(f"Chart window: {chart_start} → {chart_end}")

    print("Fetching HF forecasts …")
    hf_fc = fetch_forecasts(args.api_key, cfg["api_site_id"], cfg["project_id"],
                             "hydroforecast-seasonal", init_times,
                             source_metadata={"modelGeneration": "Seasonal-3"})
    print(f"  {len(hf_fc)} HF forecasts returned")

    print("Fetching NWRFC ESP forecasts …")
    rfc_fc = fetch_forecasts(args.api_key, cfg["api_site_id"], cfg["project_id"],
                              "nwrfc-esp-natural", init_times)
    print(f"  {len(rfc_fc)} RFC forecasts returned")

    print("Fetching historical daily LTA …")
    lta_data = fetch_lta(args.api_key, cfg["api_site_id"], cfg["project_id"],
                          chart_start, chart_end)
    print(f"  {len(lta_data.get('timestamps', []))} LTA timestamps returned")

    print("Loading observed daily flow from runoff.db …")
    obs_df = fetch_observed(args.site, chart_start, today_d)
    print(f"  {len(obs_df)} observed days available")

    print("Building Plotly figure …")
    fig = build_figure(hf_fc, rfc_fc, lta_data, obs_df,
                        cfg["label"], today_d, chart_start,
                        hf_colors, rfc_colors)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    standalone = RESULTS_DIR / f"{args.site}_hydrograph_{today_d}.html"
    fig.write_html(standalone, include_plotlyjs="cdn", full_html=True)
    print(f"Saved standalone HTML: {standalone}")

    snippet = RESULTS_DIR / f"{args.site}_hydrograph_{today_d}_snippet.html"
    snippet.write_text(fig.to_html(include_plotlyjs=False, full_html=False))
    print(f"Saved embeddable snippet: {snippet}")


if __name__ == "__main__":
    main()
