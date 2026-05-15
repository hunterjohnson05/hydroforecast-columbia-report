#!/usr/bin/env python3
"""
qq_lead_time.py
---------------
Forecast-vs-observed scatter grid (a.k.a. "Q-Q by lead time") for daily mean
discharge, stratified by lead time. Compares HydroForecast and NWRFC ESP
against observed natural-flow daily discharge for operational forecasts issued
from --start through today.

Layout: one row per lead time × two columns (HF / RFC).
Each panel:
  - scatter of forecast value (Y) vs observed value (X), one point per init date
  - dashed y = x reference line
  - point color: blue = over-forecast, red = under-forecast, gray if |err| < 5% of obs
  - metrics box top-left: MAE, MAPE, R², and pair count

Site config is a `SITES` dict — add an entry to evaluate a new site.

Usage:
    /opt/anaconda3/bin/python3 qq_lead_time.py
    /opt/anaconda3/bin/python3 qq_lead_time.py --site TDAO3W --start 2026-03-01
"""

import argparse
import os
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

API_BASE = "https://api.upstream.tech/api/v2"

# Conversion: a day's volume in KAF → that day's mean discharge in CFS.
#   1 CFS for 1 day = 86400 s × (1 acre-foot / 43560 cf) = 1.9835 AF
#   So daily_kaf / 1000 (AF/day) ÷ 1.9835 (AF/CFS-day) = mean CFS
KAF_PER_DAY_TO_CFS = 1000 / 1.9835

PROJECT_DIR = Path(__file__).parent
RESULTS_DIR = PROJECT_DIR / "results" / "qq_lead_time"
DB_PATH     = PROJECT_DIR.parent / "NWRFC obs scraper" / "runoff.db"

# Lead times are computed dynamically: every LEAD_STEP days up to the data window.
# A lead is only included if both HF and RFC have at least MIN_PAIRS_PER_LEAD
# matched (forecast, observed) points — keeps sparse panels out as the season
# starts and auto-adds new rows (70d, 80d, …) as more obs days accrue.
LEAD_STEP            = 10
MIN_PAIRS_PER_LEAD   = 5
MAX_CONSIDERED_LEAD  = 365   # safety cap; real cap comes from the data window


def candidate_leads(start_d: date, today_d: date) -> list[int]:
    """All multiples of LEAD_STEP up to the data window (today − start)."""
    max_lead = min(MAX_CONSIDERED_LEAD, (today_d - start_d).days)
    return list(range(LEAD_STEP, max_lead + 1, LEAD_STEP))

# Per-site config. Add a new HB5 ID and config block to extend coverage.
SITES = {
    "TDAO3W": {
        "label":       "The Dalles",
        "api_site_id": "shared_regional-the-dalles",
        "project_id":  "shared_regional-pacific-northwest",
    },
    # Example for future sites:
    # "WBIQ1W": {
    #     "label":       "Whitebird",
    #     "api_site_id": "shared_regional-whitebird",
    #     "project_id":  "shared_regional-pacific-northwest",
    # },
}


# ── Data fetch helpers ────────────────────────────────────────────────────────
def fetch_forecasts(api_key: str, site_id: str, project_id: str,
                    source: str, init_times: list[str], max_lead_days: int,
                    source_metadata: dict | None = None) -> list[dict]:
    """One request per source, batching all init dates."""
    query: dict = {
        "source": source,
        "columns": ["discharge_mean"],
        "siteId": site_id,
        "timeAggregation": "1D",
        "rateVolumeMode": "rate",
        "projectId": project_id,
        "unitSystem": "US",
        "initializationTimes": init_times,
        # Horizon = longest evaluated lead + a small buffer.
        "forecastLengthDays": max_lead_days + 10,
    }
    if source_metadata:
        query["sourceMetadata"] = source_metadata
    resp = requests.post(
        f"{API_BASE}/timeseries/forecasts",
        json={"queries": [query]},
        headers={"Authorization": api_key},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["forecasts"]


def fetch_observed_cfs_series(db_site_id: str, start: date, end: date) -> dict[str, float]:
    """{obs_date_iso: mean_cfs} for the natural-flow daily series in [start, end]."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT obs_date, daily_kaf FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF'
          AND obs_date >= ? AND obs_date <= ?
          AND daily_kaf IS NOT NULL
    """, (db_site_id, start.isoformat(), end.isoformat())).fetchall()
    conn.close()
    return {r[0]: float(r[1]) * KAF_PER_DAY_TO_CFS for r in rows}


# ── Pairing logic ─────────────────────────────────────────────────────────────
def build_pairs(forecasts: list[dict],
                observed_cfs: dict[str, float],
                leads: list[int],
                ) -> dict[int, list[tuple[float, float, date]]]:
    """
    For each forecast and each lead time in `leads`, look up the predicted value
    on day (init + lead) and pair it with observed on the same valid date.
    Returns {lead_days: [(forecast_cfs, observed_cfs, init_date), ...]}.
    """
    out: dict[int, list[tuple[float, float, date]]] = {lead: [] for lead in leads}
    for fc in forecasts:
        init_dt = pd.to_datetime(fc["initializationTime"])
        if init_dt.tzinfo is not None:
            init_dt = init_dt.replace(tzinfo=None)
        init_d = init_dt.date()

        valid_times = pd.DatetimeIndex(pd.to_datetime(fc["validTimes"], utc=True))
        values = fc["data"].get("discharge_mean", [])
        if not values:
            continue
        s = pd.Series(values, index=valid_times).resample("D").mean().dropna()
        forecast_by_date = {d.date().isoformat(): float(v) for d, v in s.items()}

        for lead in leads:
            valid_str = (init_d + timedelta(days=lead)).isoformat()
            fcst = forecast_by_date.get(valid_str)
            obs  = observed_cfs.get(valid_str)
            if fcst is not None and obs is not None:
                out[lead].append((fcst, obs, init_d))
    return out


# ── Metrics and styling ───────────────────────────────────────────────────────
def compute_metrics(fcsts: list[float], obs: list[float]) -> tuple[float, float, float]:
    """Return (MAE in CFS, RMSE in CFS, R²)."""
    f = np.asarray(fcsts, dtype=float)
    o = np.asarray(obs,   dtype=float)
    err = f - o
    mae  = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((o - o.mean()) ** 2))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return mae, rmse, r2


def color_for_error(forecast: float, observed: float,
                     neutral_threshold: float = 0.05) -> str:
    """Blue = over-forecast, Red = under-forecast, Gray = within ±neutral_threshold."""
    if observed <= 0:
        return "#888"
    rel = (forecast - observed) / observed
    if abs(rel) < neutral_threshold:
        return "#888"
    return "#1f77b4" if rel > 0 else "#d62728"


# ── Plot ──────────────────────────────────────────────────────────────────────
def draw_panel(ax, pairs: list[tuple[float, float, date]],
                model_label: str, lead: int) -> None:
    if not pairs:
        ax.text(0.5, 0.5, "no data", ha="center", va="center",
                transform=ax.transAxes, color="#999", fontsize=9)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(f"{model_label} — Lead {lead}d", fontsize=10)
        return

    fcsts = [p[0] for p in pairs]
    obs   = [p[1] for p in pairs]
    colors = [color_for_error(f, o) for f, o in zip(fcsts, obs)]

    ax.scatter(obs, fcsts, c=colors, s=22, alpha=0.75, edgecolors="none")

    # Square axes around the combined data range, slightly padded.
    lo = min(min(obs), min(fcsts)); hi = max(max(obs), max(fcsts))
    pad = (hi - lo) * 0.05 if hi > lo else max(1.0, hi * 0.05)
    lo -= pad; hi += pad
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.plot([lo, hi], [lo, hi], linestyle="--", color="gray", linewidth=1, alpha=0.6)

    # Metrics box (top-left, like the client example).
    mae, rmse, r2 = compute_metrics(fcsts, obs)
    txt = (f"MAE: {mae:,.0f}\n"
           f"RMSE: {rmse:,.0f}\n"
           f"R²: {r2:.3f}\n"
           f"n = {len(pairs)}")
    ax.text(0.04, 0.96, txt, transform=ax.transAxes, va="top", ha="left",
            fontsize=8, color="#222",
            bbox=dict(boxstyle="round,pad=0.4",
                      facecolor="white", alpha=0.88, edgecolor="#bbb"))

    ax.set_title(f"{model_label} — Lead {lead}d", fontsize=10)
    ax.set_xlabel("Observed (CFS)", fontsize=8)
    ax.set_ylabel("Forecast (CFS)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.3)
    # Format axis ticks with comma separators.
    fmt = plt.FuncFormatter(lambda v, _: f"{v:,.0f}")
    ax.xaxis.set_major_formatter(fmt); ax.yaxis.set_major_formatter(fmt)


def _draw_summary_table(ax, leads: list[int],
                         pairs_hf: dict, pairs_rfc: dict) -> None:
    """Render a summary metrics table: rows = lead times, cols = n + HF/RFC metrics."""
    ax.axis("off")

    col_labels = ["Lead", "n",
                   "HF MAE (CFS)", "HF RMSE (CFS)", "HF R²",
                   "RFC MAE (CFS)", "RFC RMSE (CFS)", "RFC R²"]

    rows: list[list[str]] = []
    cell_colors: list[list[str]] = []
    for lead in leads:
        hf = pairs_hf.get(lead, [])
        rfc = pairs_rfc.get(lead, [])
        hf_mae, hf_rmse, hf_r2 = compute_metrics([p[0] for p in hf],  [p[1] for p in hf])  if hf  else (np.nan,)*3
        rf_mae, rf_rmse, rf_r2 = compute_metrics([p[0] for p in rfc], [p[1] for p in rfc]) if rfc else (np.nan,)*3

        def fmt(v, kind):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return "—"
            if kind == "cfs": return f"{v:,.0f}"
            if kind == "r2":  return f"{v:.3f}"
            return str(v)

        rows.append([
            f"{lead}d", str(max(len(hf), len(rfc))),
            fmt(hf_mae, "cfs"), fmt(hf_rmse, "cfs"), fmt(hf_r2, "r2"),
            fmt(rf_mae, "cfs"), fmt(rf_rmse, "cfs"), fmt(rf_r2, "r2"),
        ])
        # Light tint per model section: HF blue-ish, RFC orange-ish
        cell_colors.append([
            "white", "white",
            "#eef4f9", "#eef4f9", "#eef4f9",
            "#fdf2e8", "#fdf2e8", "#fdf2e8",
        ])

    header_colors = ["#ddd", "#ddd",
                      "#cfe0ed", "#cfe0ed", "#cfe0ed",
                      "#f6d9bb", "#f6d9bb", "#f6d9bb"]

    # Table fills the bottom ~88% of the axes; title sits in the top 8%, leaving
    # a small gap between. The axes itself is sized in the gridspec to leave
    # near-zero blank margin above/below.
    table = ax.table(cellText=rows, colLabels=col_labels,
                     cellColours=cell_colors, colColours=header_colors,
                     bbox=[0, 0, 1, 0.88], cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for col in range(len(col_labels)):
        table[0, col].set_text_props(weight="bold")

    ax.text(0, 0.97, "Summary of metrics by lead time",
            transform=ax.transAxes, fontsize=11, fontweight="bold",
            ha="left", va="top")


def make_chart(pairs_hf: dict, pairs_rfc: dict, leads: list[int],
                site_label: str, start_d: date, out_path: Path) -> None:
    n_rows = max(len(leads), 1)
    # Reserve a fixed ~2.5-inch block for the summary table (title + table).
    # The figure top margin is also tightened to keep the suptitle close.
    table_inches = 2.5
    fig_h = 3.2 * n_rows + table_inches
    table_h_ratio = table_inches / 3.2  # height ratio relative to a scatter row
    fig = plt.figure(figsize=(10, fig_h))
    gs = fig.add_gridspec(n_rows + 1, 2,
                          height_ratios=[table_h_ratio] + [1.0] * n_rows,
                          hspace=0.55, wspace=0.25,
                          top=0.96, bottom=0.03, left=0.08, right=0.97)

    ax_table = fig.add_subplot(gs[0, :])
    _draw_summary_table(ax_table, leads, pairs_hf, pairs_rfc)

    for i, lead in enumerate(leads):
        ax_l = fig.add_subplot(gs[i + 1, 0])
        ax_r = fig.add_subplot(gs[i + 1, 1])
        draw_panel(ax_l, pairs_hf.get(lead, []),  "HydroForecast", lead)
        draw_panel(ax_r, pairs_rfc.get(lead, []), "NWRFC ESP",     lead)

    fig.suptitle(
        f"{site_label} — Daily Flow Forecast vs Observed by Lead Time\n"
        f"Operational forecasts {start_d} → {date.today()} · "
        f"Blue = over-forecast, Red = under-forecast, Gray = |err| < 5% of obs",
        fontsize=11, y=0.99,
    )
    fig.text(0.99, 0.005, f"Created {date.today()}", ha="right", va="bottom",
             fontsize=8, color="gray")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="TDAO3W", choices=list(SITES.keys()),
                        help="HB5 site ID (must exist in SITES dict)")
    parser.add_argument("--start", default=f"{date.today().year}-03-01",
                        help="First forecast init date YYYY-MM-DD (default: March 1 of current year)")
    parser.add_argument("--api-key", default=os.environ.get("HF_API_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API key required (HF_API_KEY env var or --api-key)")

    cfg = SITES[args.site]
    start_d = date.fromisoformat(args.start)
    today_d = date.today()
    leads_all = candidate_leads(start_d, today_d)
    if not leads_all:
        raise SystemExit(f"Data window too short for any {LEAD_STEP}-day lead "
                         f"(only {(today_d - start_d).days} days since {start_d})")
    max_lead = leads_all[-1]

    # Init dates: every day from start through today. Forecasts whose lead falls
    # past today's observed will simply produce no pair for that (init, lead).
    init_dates = []
    d = start_d
    while d <= today_d:
        init_dates.append(d)
        d += timedelta(days=1)

    init_times = [
        datetime(d.year, d.month, d.day, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for d in init_dates
    ]

    print(f"Site: {args.site} ({cfg['label']})")
    print(f"Init range: {start_d} → {today_d}  ({len(init_dates)} init dates)")
    print(f"Candidate leads: {leads_all}  (every {LEAD_STEP}d up to data window)")

    print("Fetching HF forecasts …")
    hf_fc = fetch_forecasts(args.api_key, cfg["api_site_id"], cfg["project_id"],
                             "hydroforecast-seasonal", init_times, max_lead,
                             source_metadata={"modelGeneration": "Seasonal-3"})
    print(f"  {len(hf_fc)} HF forecasts returned")

    print("Fetching RFC forecasts …")
    rfc_fc = fetch_forecasts(args.api_key, cfg["api_site_id"], cfg["project_id"],
                              "nwrfc-esp-natural", init_times, max_lead)
    print(f"  {len(rfc_fc)} RFC forecasts returned")

    print(f"Loading observed daily flow ({args.site}, natural) from runoff.db …")
    obs_series = fetch_observed_cfs_series(
        args.site, start_d, today_d + timedelta(days=max_lead),
    )
    print(f"  {len(obs_series)} obs days available")

    pairs_hf  = build_pairs(hf_fc, obs_series, leads_all)
    pairs_rfc = build_pairs(rfc_fc, obs_series, leads_all)

    # Keep only leads where BOTH models have enough pairs to be meaningful.
    leads_viable = [
        lead for lead in leads_all
        if len(pairs_hf.get(lead, []))  >= MIN_PAIRS_PER_LEAD
        and len(pairs_rfc.get(lead, [])) >= MIN_PAIRS_PER_LEAD
    ]
    print("Pair counts per lead time:")
    for lead in leads_all:
        kept = "✓" if lead in leads_viable else f"✗ (< {MIN_PAIRS_PER_LEAD})"
        print(f"  lead={lead:3d}d: HF n={len(pairs_hf[lead]):3d}  "
              f"RFC n={len(pairs_rfc[lead]):3d}  {kept}")

    if not leads_viable:
        raise SystemExit(f"No lead time has ≥ {MIN_PAIRS_PER_LEAD} pairs yet. "
                         f"Wait for more observed data.")

    out_path = RESULTS_DIR / f"{args.site}_qq_lead_time_{today_d}.png"
    make_chart(pairs_hf, pairs_rfc, leads_viable, cfg["label"], start_d, out_path)


if __name__ == "__main__":
    main()
