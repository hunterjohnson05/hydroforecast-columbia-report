#!/usr/bin/env python3
"""
apr_aug_forecast_evolution.py
------------------------------
Reproduces the customer-style chart: rolling 30-day evolution of Apr-Aug
seasonal volume forecasts (HF + RFC), with a dual axis showing % of normal
relative to NWRFC's published long-term average.

Bars        — Apr-Aug forecast volume (KAF) for HF and RFC
Lines       — Same forecast as % of LTA
Dashed line — 100% reference (LTA)

LTA source: scripts/lta.py — pulls 1991-2020 30-year normals from the
`lta_normals` table (populated by NWRFC obs scraper/scrape_lta_normals.py).

Usage:
    python3 apr_aug_forecast_evolution.py --api-key <key>
"""

import argparse
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
import requests

API_BASE = "https://api.upstream.tech/api/v2"
CFS_DAY_TO_MAF = 86400 / 43560 / 1_000_000   # CFS for 1 day → MAF

PROJECT_DIR = Path(__file__).parent
RESULTS_DIR = PROJECT_DIR / "results" / "apr_aug_evolution"
DB_PATH     = PROJECT_DIR.parent / "NWRFC obs scraper" / "runoff.db"

LOOKBACK_DAYS = 28

# LTA + season metadata are loaded at runtime from scripts/lta.py based on the
# --season CLI flag (default apr-aug). The boxplot script uses the same source.
from lta import get_lta_maf, parse_season


# ── Data helpers ──────────────────────────────────────────────────────────────
def fetch_observed_apr_to_date_maf(db_site_id: str, day: str) -> float:
    """
    Cumulative Apr-to-date observed volume in MAF for a given day, from the
    NWRFC scraped DB. If the exact day has no row (scraper gap), falls back
    to the most recent prior day's value so the resulting series stays smooth.
    Returns 0.0 only if no prior data exists at all (e.g. before April).
    """
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("""
        SELECT cumul_apr_to_date FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF' AND obs_date <= ?
              AND cumul_apr_to_date IS NOT NULL
        ORDER BY obs_date DESC LIMIT 1
    """, (db_site_id, day)).fetchone()
    conn.close()
    # cumul_apr_to_date column is stored in KAF — convert to MAF
    return (float(row[0]) / 1000.0) if (row and row[0] is not None) else 0.0


def fetch_forecasts(api_key: str, site_id: str, project_id: str,
                    source: str, init_times: list[str],
                    source_metadata: dict | None = None) -> list[dict]:
    query: dict = {
        "source": source,
        "columns": ["discharge_mean"],
        "siteId": site_id,
        "timeAggregation": "1D",
        "rateVolumeMode": "rate",
        "projectId": project_id,
        "unitSystem": "US",
        "initializationTimes": init_times,
        "forecastLengthDays": 210,
    }
    if source_metadata:
        query["sourceMetadata"] = source_metadata

    resp = requests.post(
        f"{API_BASE}/timeseries/forecasts",
        json={"queries": [query]},
        headers={"Authorization": api_key},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["forecasts"]


def calc_seasonal_maf(values: list, valid_times: pd.DatetimeIndex,
                       end_month: int) -> float | None:
    """Seasonal (Apr 1 → end_month) forecast volume in MAF from daily CFS values."""
    s = pd.Series(values, index=valid_times)
    year = valid_times[0].year
    season = s[(s.index.month >= 4) & (s.index.month <= end_month) & (s.index.year == year)]
    if season.empty:
        return None
    return float(season.resample("D").mean().sum() * CFS_DAY_TO_MAF)


def build_series(forecasts: list[dict], db_site_id: str, end_month: int) -> dict[str, float]:
    """
    Return {label "MM/DD": seasonal MAF} for each forecast init time.
    Seasonal total = forecast volume (Apr→end_month from init forward) + observed
    Apr-to-date on the init date. Matches pnw_volume_forecast_plot.py's accounting
    so % of normal values are consistent across both charts.
    """
    out = {}
    for fc in forecasts:
        valid_times = pd.DatetimeIndex(pd.to_datetime(fc["validTimes"], utc=True))
        values = fc["data"].get("discharge_mean")
        if not values:
            continue
        forecast_vol = calc_seasonal_maf(values, valid_times, end_month)
        if forecast_vol is None:
            continue
        init_dt = pd.to_datetime(fc["initializationTime"])
        if init_dt.tzinfo is not None:
            init_dt = init_dt.replace(tzinfo=None)
        observed_to_date = fetch_observed_apr_to_date_maf(db_site_id, init_dt.strftime("%Y-%m-%d"))
        out[init_dt.strftime("%m/%d")] = forecast_vol + observed_to_date
    return out


# ── Plot ──────────────────────────────────────────────────────────────────────
def plot(hf_vol: dict, rfc_vol: dict, lta_maf: float, site_id: str,
         out_path: Path, season_label: str):
    labels = sorted(set(hf_vol) | set(rfc_vol),
                    key=lambda s: tuple(int(x) for x in s.split("/")))
    n = len(labels)
    x = np.arange(n)

    hf_v  = [hf_vol.get(l)  for l in labels]
    rfc_v = [rfc_vol.get(l) for l in labels]
    hf_pct  = [(v / lta_maf * 100) if v is not None else None for v in hf_v]
    rfc_pct = [(v / lta_maf * 100) if v is not None else None for v in rfc_v]

    width = 0.4
    fig, ax_l = plt.subplots(figsize=(13, 5.5))
    ax_r = ax_l.twinx()

    # Bars
    ax_l.bar(x - width / 2, [v or 0 for v in hf_v],  width,
             label=f"HF {season_label} (MAF)",  color="#1f77b4", alpha=0.55)
    ax_l.bar(x + width / 2, [v or 0 for v in rfc_v], width,
             label=f"RFC {season_label} (MAF)", color="#ff7f0e", alpha=0.55)

    # Lines
    ax_r.plot(x, hf_pct,  marker="o", linewidth=1.6,
              color="#0d3b66", label="HF % of Normal")
    ax_r.plot(x, rfc_pct, marker="o", linewidth=1.6,
              color="#a04000", label="RFC % of Normal")

    # Data labels: % of normal above each line marker. Label every point on the
    # latest (rightmost) date plus every 3rd point earlier to avoid clutter.
    # Stack HF (top) and RFC (bottom) labels above the highest of the two values,
    # mirroring the 14-day boxplot label style.
    label_indices = set(range(0, n, 3)) | {n - 1}
    for xi, hp, rp in zip(x, hf_pct, rfc_pct):
        if xi not in label_indices:
            continue
        top = max(v for v in [hp, rp] if v is not None)
        if hp is not None:
            ax_r.annotate(f"HF:{hp:.0f}%", xy=(xi, top), xytext=(0, 22),
                          textcoords="offset points",
                          ha="center", fontsize=9, color="#0d3b66", fontweight="bold")
        if rp is not None:
            ax_r.annotate(f"RFC:{rp:.0f}%", xy=(xi, top), xytext=(0, 8),
                          textcoords="offset points",
                          ha="center", fontsize=9, color="#a04000", fontweight="bold")

    # LTA reference line (label added to legend below, not annotated on chart)
    ax_r.axhline(100, linestyle="--", color="gray", linewidth=1.0, alpha=0.7)

    # Axes
    ax_l.set_xlabel("Forecast Issue Date", fontsize=12)
    ax_l.set_ylabel(f"{season_label} Volume (MAF)", color="#1f77b4", fontsize=12)
    ax_r.set_ylabel("% of Normal", color="#a04000", fontsize=12)
    ax_l.tick_params(axis="y", colors="#1f77b4", labelsize=11)
    ax_r.tick_params(axis="y", colors="#a04000", labelsize=11)
    ax_l.set_xticks(x)
    ax_l.set_xticklabels(labels, rotation=45, ha="right", fontsize=10)
    ax_l.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}"))
    ax_l.grid(axis="y", alpha=0.3)
    ax_l.spines["top"].set_visible(False)
    ax_r.spines["top"].set_visible(False)

    # Y-limits: bars use 0 → ~120% of LTA so % axis aligns naturally
    ax_l.set_ylim(0, lta_maf * 1.2)
    ax_r.set_ylim(0, 120)

    # Combined legend — above the plot, LTA entry added manually
    h1, l1 = ax_l.get_legend_handles_labels()
    h2, l2 = ax_r.get_legend_handles_labels()
    lta_handle = Line2D([0], [0], color="gray", linestyle="--", linewidth=1.0,
                        label=f"LTA ({lta_maf:.1f} MAF)")
    ax_l.legend(
        h1 + h2 + [lta_handle], l1 + l2 + [lta_handle.get_label()],
        loc="upper center", bbox_to_anchor=(0.5, 1.18),
        ncols=3, fontsize=10, frameon=True,
    )

    fig.suptitle(f"The Dalles — {season_label} Forecast Evolution (Past {LOOKBACK_DAYS} Days)",
                 fontsize=15)
    fig.text(0.99, 0.01, f"Created {datetime.now().strftime('%Y-%m-%d')}",
             ha="right", va="bottom", fontsize=10, color="gray")

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-id",    default="shared_regional-the-dalles")
    parser.add_argument("--project-id", default="shared_regional-pacific-northwest")
    parser.add_argument("--api-key",    default=os.environ.get("HF_API_KEY"))
    parser.add_argument("--db-site",    default="TDAO3W")
    parser.add_argument("--season", choices=["apr-aug", "apr-sep"], default="apr-aug",
                        help="Seasonal window for cumulative volume + LTA (default: apr-aug)")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API key required (HF_API_KEY env var or --api-key)")

    season = parse_season(args.season)
    lta_maf = get_lta_maf(args.db_site, months=season["months"])

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    init_times = [(today - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
                  for i in range(LOOKBACK_DAYS - 1, -1, -1)]

    print(f"Season: {season['label']}  ·  LTA = {lta_maf:.2f} MAF "
          f"(via lta.py, site {args.db_site})")

    print(f"Fetching {LOOKBACK_DAYS} days of HF forecasts …")
    hf_fc  = fetch_forecasts(args.api_key, args.site_id, args.project_id,
                             "hydroforecast-seasonal", init_times,
                             source_metadata={"modelGeneration": "Seasonal-3"})
    print(f"Fetching {LOOKBACK_DAYS} days of RFC forecasts …")
    rfc_fc = fetch_forecasts(args.api_key, args.site_id, args.project_id,
                             "nwrfc-esp-natural", init_times)

    hf_vol  = build_series(hf_fc,  args.db_site, end_month=season["end_month"])
    rfc_vol = build_series(rfc_fc, args.db_site, end_month=season["end_month"])
    print(f"HF: {len(hf_vol)} forecasts, RFC: {len(rfc_vol)} forecasts")

    out_path = RESULTS_DIR / f"the_dalles_{season['slug']}_evolution_{date_today_str()}.png"
    plot(hf_vol, rfc_vol, lta_maf, args.site_id, out_path,
         season_label=season["label"])


def date_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    main()
