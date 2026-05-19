#!/usr/bin/env python3
"""
forecast_bar_chart.py
----------------------
Generates one grouped bar chart per monthly initialization date (Apr 1, May 1, …),
comparing HydroForecast, RFC, and observed cumulative volumes at The Dalles.

Each chart shows cumulative volume from its own init date onward — so the May chart
resets to zero at May 1 and shows May-to-date, independently of the April chart.

Usage:
    python3 forecast_bar_chart.py
    python3 forecast_bar_chart.py --year 2026
"""

import argparse
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import requests

# ── Paths / config ─────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent

_env = PROJECT_DIR / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("UPSTREAM_API_KEY", "")
API_URL = "https://api.upstream.tech/api/v2/timeseries/forecasts"
DB_PATH = str(PROJECT_DIR / "runoff.db")
OUT_DIR = PROJECT_DIR.parent / "scripts" / "results" / "bar_chart"

CFS_TO_TAF   = 1.9835 / 1000
INIT_HOUR_UTC = 17   # API init is midnight UTC = 17:00 PDT the previous day


# ── Site configuration ─────────────────────────────────────────────────────────
SITES = [
    {
        "label":       "The Dalles",
        "api_site_id": "shared_regional-the-dalles",
        "db_site_id":  "TDAO3W",
        "project_id":  "shared_regional-pacific-northwest",
    },
]


# ── Date helpers ───────────────────────────────────────────────────────────────
def monthly_init_dates(year: int) -> list[date]:
    """Return Apr 1, May 1, … through the current month (inclusive)."""
    today = date.today()
    inits = []
    for month in range(4, 13):
        d = date(year, month, 1)
        if d > today:
            break
        inits.append(d)
    return inits


# ── API helpers ────────────────────────────────────────────────────────────────
def call_api(api_site_id: str, project_id: str, init_iso: str) -> dict:
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
                "initializationTimes": [init_iso],
            },
            {
                # hydroforecast-seasonal = blended ERA5+GEFS mean — matches HF dashboard.
                # Do NOT use hydroforecast-seasonal-3-era5 alone (~35% lower).
                "source": "hydroforecast-seasonal",
                "columns": ["discharge_mean"],
                "sourceMetadata": {"modelGeneration": "Seasonal-3"},
                "forecastLengthDays": 365,
                "siteId": api_site_id,
                "timeAggregation": "1D",
                "rateVolumeMode": "rate",
                "projectId": project_id,
                "unitSystem": "US",
                "initializationTimes": [init_iso],
            },
        ]
    }
    r = requests.post(API_URL, headers={"Authorization": API_KEY}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_daily_cfs(result: dict) -> dict[str, float]:
    forecasts = result.get("forecasts", [])
    if not forecasts:
        return {}
    fc = forecasts[0]
    daily = {}
    for t, v in zip(fc.get("validTimes", []), fc.get("data", {}).get("discharge_mean", [])):
        if v is not None:
            daily[t[:10]] = v
    return daily


def accumulate(daily_cfs: dict[str, float], start: date, end: date) -> float:
    """
    Sum daily CFS → TAF from start through end.
    The first day uses a 7/24 partial-day weight to match the HF dashboard,
    which accumulates from the 17:00 UTC init time rather than from midnight.
    """
    running = 0.0
    d = start
    first = True
    while d <= end:
        v = daily_cfs.get(d.isoformat())
        if v is not None:
            weight = (24 - INIT_HOUR_UTC) / 24 if first else 1.0
            running += v * CFS_TO_TAF * weight
        d += timedelta(days=1)
        first = False
    return round(running, 1)


# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_obs_baseline(db_site_id: str, day: date) -> float:
    """
    Return the cumul_apr_to_date value for a given day (the day before init).
    This is subtracted from each snapshot to make volumes relative to the init date.
    Returns 0.0 if the day is before April (nothing accumulated yet).
    """
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("""
        SELECT cumul_apr_to_date FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF' AND obs_date = ?
    """, (db_site_id, day.isoformat())).fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else 0.0


TARGET_DAYS = [7, 14, 21, 28]


MAX_TARGET_DISTANCE_DAYS = 2  # closest obs must be within this many days of a target


def get_snapshot_obs(db_site_id: str, start: date, end: date) -> list[tuple[str, float]]:
    """
    Return (obs_date, cumul_apr_to_date) for dates closest to the 7th, 14th,
    21st, and 28th of each month in [start, end].

    A target is only included if its closest available obs is within
    MAX_TARGET_DISTANCE_DAYS — this prevents future targets from collapsing
    onto today's date and creating near-duplicate "latest" snapshots.
    """
    conn = sqlite3.connect(DB_PATH)
    all_rows = conn.execute("""
        SELECT obs_date, cumul_apr_to_date
        FROM runoff_observations
        WHERE site_id = ? AND row_type = 'RUNOFF'
          AND obs_date >= ? AND obs_date <= ?
        ORDER BY obs_date
    """, (db_site_id, start.isoformat(), end.isoformat())).fetchall()
    conn.close()
    if not all_rows:
        return []

    by_date = {r[0]: r[1] for r in all_rows}
    all_dates = sorted(by_date)

    # Build target dates: 7th, 14th, 21st, 28th of each month in the init month
    # range. Targets may extend past `end` — we'll snap to the closest available obs.
    targets: list[date] = []
    year, month = start.year, start.month
    while True:
        for day in TARGET_DAYS:
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d >= start:
                targets.append(d)
        if (year, month) == (end.year, end.month):
            break
        month += 1
        if month > 12:
            month, year = 1, year + 1

    # For each target, pick the closest available obs date.
    # Require at least 5 days of data (target must be >= 5 days after start)
    # AND the closest available obs must be within MAX_TARGET_DISTANCE_DAYS of
    # the target — otherwise the target hasn't really "happened yet" and we
    # skip it.
    selected: dict[str, float] = {}
    for t in targets:
        if (t - start).days < 5:
            continue
        closest_str = min(all_dates, key=lambda d: abs((date.fromisoformat(d) - t).days))
        closest_date = date.fromisoformat(closest_str)
        if abs((closest_date - t).days) > MAX_TARGET_DISTANCE_DAYS:
            continue
        selected[closest_str] = by_date[closest_str]

    return sorted(selected.items())


def get_latest_obs_date(db_site_id: str) -> date:
    conn = sqlite3.connect(DB_PATH)
    latest = conn.execute(
        "SELECT MAX(obs_date) FROM runoff_observations WHERE site_id = ? AND row_type = 'RUNOFF'",
        (db_site_id,)
    ).fetchone()[0]
    conn.close()
    return date.fromisoformat(latest)


# ── Chart ──────────────────────────────────────────────────────────────────────
def make_chart(site_label: str, snap_rows: list[dict], init_date: date, out_path: Path,
               title: str | None = None) -> None:
    labels   = [s["label"] for s in snap_rows]
    obs_vals = [s["obs"]   for s in snap_rows]
    hf_vals  = [s["hf"]    for s in snap_rows]
    rfc_vals = [s["rfc"]   for s in snap_rows]
    hf_pct   = [(s["hf"]  - s["obs"]) / s["obs"] * 100 if s["obs"] else 0 for s in snap_rows]
    rfc_pct  = [(s["rfc"] - s["obs"]) / s["obs"] * 100 if s["obs"] else 0 for s in snap_rows]

    n       = len(snap_rows)
    x       = np.arange(n)
    width   = 0.24
    offsets = [-width, 0, width]
    colors  = {"obs": "#4a4a4a", "hf": "#1f77b4", "rfc": "#ff7f0e"}

    fig, ax = plt.subplots(figsize=(max(7, n * 2.5), 5))

    ax.bar(x + offsets[0], obs_vals, width, label="Observed (NWRFC)",
           color=colors["obs"], alpha=0.85)
    bars_hf  = ax.bar(x + offsets[1], hf_vals, width, label="HydroForecast",
                      color=colors["hf"], alpha=0.85)
    bars_rfc = ax.bar(x + offsets[2], rfc_vals, width, label="NWRFC ESP Natural",
                      color=colors["rfc"], alpha=0.85)

    for bar, pct, obs in zip(bars_hf, hf_pct, obs_vals):
        sign  = "+" if pct >= 0 else ""
        y_pos = bar.get_height() + 80 if pct >= 0 else obs - 600
        va    = "bottom" if pct >= 0 else "top"
        color = "white" if pct < 0 else "#1a4f7a"   # white inside bar; dark blue above
        ax.text(bar.get_x() + bar.get_width() / 2, y_pos,
                f"{sign}{pct:.1f}%", ha="center", va=va,
                fontsize=8.5, color=color, fontweight="bold")

    for bar, pct in zip(bars_rfc, rfc_pct):
        sign  = "+" if pct >= 0 else ""
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 80,
                f"{sign}{pct:.1f}%", ha="center", va="bottom",
                fontsize=8.5, color="#c0580a", fontweight="bold")   # always dark orange

    for i, obs in enumerate(obs_vals):
        ax.hlines(obs,
                  x[i] + offsets[0] - width * 0.6,
                  x[i] + offsets[2] + width * 0.6,
                  colors=colors["obs"], linewidths=1.2, linestyles="--", alpha=0.6)

    init_label = init_date.strftime("%b %-d, %Y")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel(f"Cumulative Volume from {init_label} (TAF)", fontsize=10)
    _title = title or (f"{site_label} — Cumulative Volume Forecast Comparison\n"
                       f"{init_label} initialization")
    ax.set_title(_title, fontsize=10, pad=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    # Include hf_vals when computing y-limit so positive HF errors stay below the title.
    max_val = max(rfc_vals + obs_vals + hf_vals) if (rfc_vals or obs_vals or hf_vals) else 1
    ax.set_ylim(0, max_val * 1.22)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", fontsize=9, frameon=True)

    fig.text(0.99, 0.01, f"Created {date.today()}", ha="right", va="bottom",
             fontsize=8, color="gray")

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_path}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=date.today().year)
    args = parser.parse_args()

    # Clear stale init PNGs so the report never shows data from a prior run
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*_init.png"):
        stale.unlink()

    init_dates = monthly_init_dates(args.year)
    print(f"Init dates: {[d.isoformat() for d in init_dates]}")

    for site in SITES:
        label       = site["label"]
        api_site_id = site["api_site_id"]
        db_site_id  = site["db_site_id"]
        project_id  = site["project_id"]

        period_end = get_latest_obs_date(db_site_id)
        print(f"\nProcessing: {label}  (obs through {period_end})")

        for init_date in init_dates:
            month_name = init_date.strftime("%b").lower()
            init_iso   = f"{init_date.isoformat()}T00:00:00.000Z"
            accum_start = init_date - timedelta(days=1)  # partial first day (17:00 UTC init)

            # Baseline: cumulative volume at end of the day before init.
            # Subtracting this makes all volumes relative to the init date.
            baseline = get_obs_baseline(db_site_id, accum_start)

            print(f"\n  {init_date} init  (baseline obs = {baseline:,.0f} TAF)")
            print(f"  Calling API …")

            resp    = call_api(api_site_id, project_id, init_iso)
            results = resp.get("data", [])
            if len(results) < 2:
                print("  WARNING: API returned fewer than 2 results — skipping")
                continue

            rfc_daily = parse_daily_cfs(results[0])
            hf_daily  = parse_daily_cfs(results[1])

            # Cap snapshots at the end of the init month so each chart stays
            # within its own month. For the current month, use latest available obs.
            if init_date.month == 12:
                month_end = date(init_date.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(init_date.year, init_date.month + 1, 1) - timedelta(days=1)
            snap_end = min(period_end, month_end)

            obs_rows = get_snapshot_obs(db_site_id, init_date, snap_end)
            if not obs_rows:
                print(f"  WARNING: no observed data from {init_date} — skipping")
                continue

            snap_rows = []
            for obs_date_str, cumul_apr_to_date in obs_rows:
                snap_date = date.fromisoformat(obs_date_str)
                obs_val   = round(cumul_apr_to_date - baseline, 1)
                hf_val    = accumulate(hf_daily,  accum_start, snap_date)
                rfc_val   = accumulate(rfc_daily, accum_start, snap_date)
                d_label   = snap_date.strftime("%b %-d")
                snap_rows.append({"label": d_label, "obs": obs_val, "hf": hf_val, "rfc": rfc_val})
                print(f"    {obs_date_str}: obs={obs_val:,.0f}  hf={hf_val:,.0f}  rfc={rfc_val:,.0f} TAF")

            slug     = label.lower().replace(" ", "_")
            out_path = OUT_DIR / f"{slug}_{month_name}_init.png"
            make_chart(label, snap_rows, init_date, out_path)

        # ── Apr 1 Season chart (fixed Apr 1 init, all snapshots Apr 1 → today) ──
        today_date = date.today()
        if today_date >= date(args.year, 4, 1):
            apr1_init   = date(args.year, 4, 1)
            apr1_iso    = f"{apr1_init.isoformat()}T00:00:00.000Z"
            accum_start = apr1_init - timedelta(days=1)
            baseline_apr1 = get_obs_baseline(db_site_id, accum_start)

            print(f"\n  [Apr 1 Season chart]  {apr1_init} init  "
                  f"(baseline obs = {baseline_apr1:,.0f} TAF, obs through {period_end})")
            print(f"  Calling API …")

            resp_apr1    = call_api(api_site_id, project_id, apr1_iso)
            results_apr1 = resp_apr1.get("data", [])
            if len(results_apr1) >= 2:
                rfc_daily_apr1 = parse_daily_cfs(results_apr1[0])
                hf_daily_apr1  = parse_daily_cfs(results_apr1[1])
                # No month cap — snapshots span Apr 1 through latest obs date
                obs_rows_apr1 = get_snapshot_obs(db_site_id, apr1_init, period_end)
                if obs_rows_apr1:
                    snap_rows_apr1 = []
                    for obs_date_str, cumul_apr_to_date in obs_rows_apr1:
                        snap_date = date.fromisoformat(obs_date_str)
                        obs_val   = round(cumul_apr_to_date - baseline_apr1, 1)
                        hf_val    = accumulate(hf_daily_apr1,  accum_start, snap_date)
                        rfc_val   = accumulate(rfc_daily_apr1, accum_start, snap_date)
                        d_label   = snap_date.strftime("%b %-d")
                        snap_rows_apr1.append(
                            {"label": d_label, "obs": obs_val, "hf": hf_val, "rfc": rfc_val}
                        )
                        print(f"    {obs_date_str}: obs={obs_val:,.0f}  "
                              f"hf={hf_val:,.0f}  rfc={rfc_val:,.0f} TAF")
                    slug     = label.lower().replace(" ", "_")
                    out_path = OUT_DIR / f"{slug}_apr1_season_init.png"
                    make_chart(
                        label, snap_rows_apr1, apr1_init, out_path,
                        title=(f"{label} — Apr 1 Init: Cumulative Volume Season to Date\n"
                               f"Apr 1, {args.year} initialization · snapshots through {period_end}"),
                    )
                else:
                    print("  WARNING: no observed data from Apr 1 — skipping Apr 1 Season chart")
            else:
                print("  WARNING: API returned fewer than 2 results for Apr 1 — skipping")


if __name__ == "__main__":
    main()
