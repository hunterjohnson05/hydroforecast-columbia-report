"""
Plot how Apr-Aug volume forecasts (HF and NWRFC) have evolved over the past 2 weeks.
Observed Apr-Aug volume to date is fetched from the NWRFC runoff summary page and added
to each forecast so all bars represent the full Apr-Aug total.

Usage:
    HF_API_KEY=<your_key> python backend/scripts/pnw_volume_forecast_plot.py
    python backend/scripts/pnw_volume_forecast_plot.py --api-key <your_key> --site-id shared_regional-the-dalles
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

API_BASE = "https://api.upstream.tech/api/v2"

# CFS * 1 day -> MAF
# 1 day = 86400 seconds, 1 acre-foot = 43560 cubic feet, 1 MAF = 1e6 acre-feet
CFS_DAY_TO_MAF = 86400 / 43560 / 1_000_000

QUANTILE_COLUMNS = ["discharge_q0.05", "discharge_q0.25", "discharge_mean", "discharge_q0.75", "discharge_q0.95"]


def fetch_forecasts(
    api_key: str,
    site_id: str,
    project_id: str,
    source: str,
    init_times: list[str],
    source_metadata: dict | None = None,
) -> list[dict]:
    query: dict = {
        "source": source,
        "columns": QUANTILE_COLUMNS,
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


def fetch_observed_apr_aug_maf(date_str: str, nwrfc_id: str) -> tuple[float, float | None]:
    """
    Fetch the observed Apr 1 to date cumulative runoff and % of normal from the NWRFC runoff summary.

    date_str: MM/DD/YYYY
    Returns (maf, pct_avg) — pct_avg is None if not available.
    Returns (0.0, None) for dates before April 1 or if the page has no data.
    """
    month = int(date_str.split("/")[0])
    if month < 4:
        return 0.0, None

    url = f"https://www.nwrfc.noaa.gov/runoff/runoff_summary.php?date={date_str}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Warning: could not fetch observed runoff for {date_str}: {e}")
        return 0.0, None

    def _last_cell(row_label: str) -> str:
        match = re.search(
            r"<td[^>]*>" + re.escape(row_label) + r"</td>\s*<td[^>]*>\s*" + re.escape(nwrfc_id) + r"\s*</td>(.*?)</tr>",
            resp.text,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return ""
        cells = re.findall(r"<td[^>]*>(.*?)</td>", match.group(1), re.DOTALL)
        return cells[-1].strip() if cells else ""

    maf = 0.0
    raw_runoff = _last_cell("RUNOFF")
    try:
        maf = float(raw_runoff) / 1000  # KAF to MAF
    except ValueError:
        pass

    pct_avg: float | None = None
    raw_pct = _last_cell("PCT AVG")
    try:
        pct_avg = float(raw_pct)
    except ValueError:
        pass

    return maf, pct_avg


def calc_seasonal_volume_maf(values: list, valid_times: pd.DatetimeIndex,
                              end_month: int) -> float | None:
    """Return seasonal forecast volume (Apr 1 → end_month) in MAF from daily CFS values."""
    s = pd.Series(values, index=valid_times)
    current_year = valid_times[0].year
    season = s[(s.index.month >= 4) & (s.index.month <= end_month) & (s.index.year == current_year)]
    if season.empty:
        return None
    # Resample to one value per calendar day to handle any duplicate or sub-daily timestamps
    season_daily = season.resample("D").mean()
    return float(season_daily.sum() * CFS_DAY_TO_MAF)


def build_boxplot_stats(
    forecasts: list[dict],
    observed_by_label: dict[str, float],
    source_label: str,
    end_month: int,
    debug: bool = False,
) -> list[dict]:
    """
    Return a list of bxp-compatible stat dicts, one per forecast initialization time.
    Each stat's volumes include the observed Apr-to-date volume added to the forecast,
    so values represent the full seasonal (Apr → end_month) total.
    Keys: whislo, q1, med, q3, whishi, label (MM/DD date string).
    """
    if debug and forecasts:
        _print_debug_sample(forecasts[-1], source_label, end_month)

    stats = []
    for fc in forecasts:
        valid_times = pd.DatetimeIndex(pd.to_datetime(fc["validTimes"], utc=True))
        data = fc["data"]

        volumes = {}
        for col in QUANTILE_COLUMNS:
            values = data.get(col)
            if values:
                vol = calc_seasonal_volume_maf(values, valid_times, end_month)
                if vol is not None:
                    volumes[col] = vol

        required = {"discharge_q0.05", "discharge_q0.25", "discharge_mean", "discharge_q0.75", "discharge_q0.95"}
        if not required.issubset(volumes):
            continue

        init_dt = pd.to_datetime(fc["initializationTime"])
        if init_dt.tzinfo is not None:
            init_dt = init_dt.replace(tzinfo=None)
        label = init_dt.strftime("%m/%d")
        observed = observed_by_label.get(label, 0.0)

        stats.append(
            {
                "whislo": volumes["discharge_q0.05"] + observed,
                "q1": volumes["discharge_q0.25"] + observed,
                "med": volumes["discharge_mean"] + observed,
                "q3": volumes["discharge_q0.75"] + observed,
                "whishi": volumes["discharge_q0.95"] + observed,
                "label": label,
            }
        )

    return sorted(stats, key=lambda s: s["label"])


def _print_debug_sample(fc: dict, label: str, end_month: int) -> None:
    valid_times = pd.DatetimeIndex(pd.to_datetime(fc["validTimes"], utc=True))
    dm = fc["data"].get("discharge_mean", [])
    s = pd.Series(dm, index=valid_times)
    current_year = valid_times[0].year
    apr_aug = s[(s.index.month >= 4) & (s.index.month <= end_month) & (s.index.year == current_year)]
    print(f"\n[debug] {label} — most recent forecast ({fc['initializationTime']})")
    print(f"  total values: {len(dm)}, Apr-Aug values: {len(apr_aug)}")
    if len(dm) >= 5:
        print(f"  first 5 discharge_mean (CFS): {[round(v, 0) for v in dm[:5]]}")
    if not apr_aug.empty:
        apr_aug_daily = apr_aug.resample("D").mean()
        print(f"  Apr-Aug daily mean (CFS): {apr_aug_daily.mean():.0f}")
        print(f"  Apr-Aug forecast total (MAF): {apr_aug_daily.sum() * CFS_DAY_TO_MAF:.2f}")


from lta import get_lta_maf, parse_season


def plot_volume_forecasts(
    hf_stats: list[dict],
    nwrfc_stats: list[dict],
    observed_by_label: dict[str, float],
    pct_avg_by_label: dict[str, float],
    site_id: str,
    output_path: str,
    season_label: str,
    historical_mean_maf: float,
) -> None:
    if not hf_stats and not nwrfc_stats:
        print("No data to plot.")
        return

    all_labels = sorted(set(s["label"] for s in hf_stats + nwrfc_stats))
    hf_by_label = {s["label"]: s for s in hf_stats}
    nwrfc_by_label = {s["label"]: s for s in nwrfc_stats}

    all_values = [v for stats in (hf_stats, nwrfc_stats) for s in stats for v in (s["whislo"], s["whishi"])]
    y_min = min(all_values) * 0.95
    y_max = max(all_values) * 1.05

    offset = 0.22
    hf_positions = [i - offset for i in range(len(all_labels))]
    nwrfc_positions = [i + offset for i in range(len(all_labels))]

    fig, (ax_forecast, ax_obs) = plt.subplots(
        2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 1]}, sharex=False
    )

    # --- Forecast boxplot subplot ---
    _draw_boxplots(
        ax_forecast,
        [hf_by_label[l] for l in all_labels if l in hf_by_label],
        [hf_positions[i] for i, l in enumerate(all_labels) if l in hf_by_label],
        color="#1f77b4",
    )
    _draw_boxplots(
        ax_forecast,
        [nwrfc_by_label[l] for l in all_labels if l in nwrfc_by_label],
        [nwrfc_positions[i] for i, l in enumerate(all_labels) if l in nwrfc_by_label],
        color="#ff7f0e",
    )
    ax_forecast.axhline(
        historical_mean_maf,
        color="gray",
        linestyle="--",
        linewidth=1.2,
        alpha=0.8,
        label=f"Hist. Mean ({historical_mean_maf:.1f} MAF)",
    )
    ax_forecast.set_ylim(y_min, y_max)
    ax_forecast.set_ylabel(f"{season_label} Volume (MAF)", fontsize=12)
    ax_forecast.set_xticks(range(len(all_labels)))
    ax_forecast.set_xticklabels(all_labels, rotation=45, ha="right", fontsize=11)
    ax_forecast.tick_params(axis="y", labelsize=11)
    ax_forecast.grid(axis="y", alpha=0.3)
    ax_forecast.legend(
        handles=[
            Patch(facecolor="#1f77b4", alpha=0.6, label="HydroForecast"),
            Patch(facecolor="#ff7f0e", alpha=0.6, label="NWRFC ESP Natural"),
            Line2D(
                [0], [0], color="gray", linestyle="--", linewidth=1.2, label=f"Hist. Mean ({historical_mean_maf:.1f} MAF)"
            ),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, 1.12),
        ncols=3,
        frameon=True,
    )

    # Annotate % of normal above each pair of boxplots
    for i, label in enumerate(all_labels):
        hf_s = hf_by_label.get(label)
        nwrfc_s = nwrfc_by_label.get(label)
        annotation_lines = []
        if hf_s:
            pct = hf_s["med"] / historical_mean_maf * 100
            annotation_lines.append(f"HF:{pct:.0f}%")
        if nwrfc_s:
            pct = nwrfc_s["med"] / historical_mean_maf * 100
            annotation_lines.append(f"RFC:{pct:.0f}%")
        if annotation_lines:
            top = max(
                (hf_s["whishi"] if hf_s else 0),
                (nwrfc_s["whishi"] if nwrfc_s else 0),
            )
            ax_forecast.text(
                i,
                top + (y_max - y_min) * 0.01,
                "\n".join(annotation_lines),
                ha="center",
                va="bottom",
                fontsize=9,
                color="black",
            )

    # --- Observed volume subplot ---
    obs_labels = [l for l in all_labels if l in observed_by_label]
    obs_values = [observed_by_label[l] for l in obs_labels]
    obs_positions = [all_labels.index(l) for l in obs_labels]
    ax_obs.bar(obs_positions, obs_values, color="#2ca02c", alpha=0.7, width=0.6)
    obs_y_max = max(obs_values) if obs_values else 1.0
    for pos, lbl, val in zip(obs_positions, obs_labels, obs_values):
        pct = pct_avg_by_label.get(lbl)
        if pct is not None:
            ax_obs.text(pos, val + obs_y_max * 0.02, f"{pct:.0f}%", ha="center", va="bottom", fontsize=10)
    ax_obs.set_ylabel("Observed\nVol (MAF)", fontsize=12)
    ax_obs.set_xticks(range(len(all_labels)))
    ax_obs.set_xticklabels(all_labels, rotation=45, ha="right", fontsize=11)
    ax_obs.set_xlabel("Forecast Issue Date", fontsize=12)
    ax_obs.tick_params(axis="y", labelsize=11)
    ax_obs.grid(axis="y", alpha=0.3)

    fig.suptitle(f"{season_label} Volume Forecast Evolution - Past 2 Weeks\n{site_id}", fontsize=16)
    fig.text(0.99, 0.99, f"Created {datetime.now().strftime('%Y-%m-%d')}", ha="right", va="top", fontsize=10, color="gray")
    plt.tight_layout()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved to {output_path}")


def _draw_boxplots(ax, stats: list[dict], positions: list[float], color: str) -> None:
    if not stats:
        return
    bp = ax.bxp(
        stats,
        positions=positions,
        showfliers=False,
        patch_artist=True,
        widths=0.35,
    )
    for patch in bp["boxes"]:
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
    for element in ("whiskers", "caps", "medians"):
        for line in bp[element]:
            line.set_color(color)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot seasonal volume forecast evolution for a PNW site.")
    parser.add_argument("--site-id", default="shared_regional-the-dalles")
    parser.add_argument("--project-id", default="shared_regional-pacific-northwest")
    parser.add_argument("--api-key", default=os.environ.get("HF_API_KEY"))
    parser.add_argument("--nwrfc-id", default="TDAO3W",
                        help="NWRFC site ID for observed runoff lookup. Default TDAO3W "
                             "(natural/unregulated) matches the natural-flow forecasts and LTA. "
                             "Using TDAO3 (regulated) would mix observed-regulated with forecast-natural.")
    parser.add_argument("--db-site",  default="TDAO3W",
                        help="HB5 ID used for the LTA lookup in runoff.db (natural/unregulated)")
    parser.add_argument("--season", choices=["apr-aug", "apr-sep"], default="apr-aug",
                        help="Seasonal window for cumulative volume + LTA (default: apr-aug)")
    today_str = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument("--output", default=None,
                        help="Output PNG path; default includes season slug + today's date")
    parser.add_argument("--debug", action="store_true", help="Print sample values and save raw API responses")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API key required: set HF_API_KEY env var or pass --api-key")

    season = parse_season(args.season)
    historical_mean_maf = get_lta_maf(args.db_site, months=season["months"])
    print(f"Season: {season['label']}  ·  LTA = {historical_mean_maf:.2f} MAF  "
          f"(via lta.py, site {args.db_site})")

    if args.output is None:
        args.output = f"results/volume_forecast_plot/pnw_volume_forecast_plot_{season['slug']}_{today_str}.png"

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    init_times = [(today - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(13, -1, -1)]

    print(f"Fetching HF forecasts for {args.site_id}...")
    hf_forecasts = fetch_forecasts(
        args.api_key,
        args.site_id,
        args.project_id,
        "hydroforecast-seasonal",
        init_times,
        source_metadata={"modelGeneration": "Seasonal-3"},
    )

    print(f"Fetching NWRFC forecasts for {args.site_id}...")
    nwrfc_forecasts = fetch_forecasts(
        args.api_key,
        args.site_id,
        args.project_id,
        "nwrfc-esp-natural",
        init_times,
    )

    if args.debug:
        debug_path = Path(args.output).with_suffix("")
        hf_path = debug_path.parent / (debug_path.name + "_hf_raw.json")
        nwrfc_path = debug_path.parent / (debug_path.name + "_nwrfc_raw.json")
        hf_path.parent.mkdir(parents=True, exist_ok=True)
        hf_path.write_text(json.dumps(hf_forecasts, indent=2))
        nwrfc_path.write_text(json.dumps(nwrfc_forecasts, indent=2))
        print(f"Raw API data saved to {hf_path} and {nwrfc_path}")

    # Collect all unique local dates from both forecast sets
    all_forecasts = hf_forecasts + nwrfc_forecasts
    unique_date_strs: set[str] = set()
    for fc in all_forecasts:
        init_dt = pd.to_datetime(fc["initializationTime"])
        if init_dt.tzinfo is not None:
            init_dt = init_dt.replace(tzinfo=None)
        unique_date_strs.add(init_dt.strftime("%m/%d/%Y"))

    print(f"Fetching observed Apr-to-date volumes from NWRFC ({args.nwrfc_id})...")
    observed_by_label: dict[str, float] = {}
    pct_avg_by_label: dict[str, float] = {}
    for date_str in sorted(unique_date_strs):
        obs, pct = fetch_observed_apr_aug_maf(date_str, args.nwrfc_id)
        label = date_str[:5]  # MM/DD
        observed_by_label[label] = obs
        if pct is not None:
            pct_avg_by_label[label] = pct
        pct_str = f"{pct:.0f}%" if pct is not None else "n/a"
        print(f"  {label}: {obs:.3f} MAF observed ({pct_str} of normal)")

    hf_stats = build_boxplot_stats(hf_forecasts, observed_by_label, "HF",
                                    end_month=season["end_month"], debug=args.debug)
    nwrfc_stats = build_boxplot_stats(nwrfc_forecasts, observed_by_label, "NWRFC",
                                       end_month=season["end_month"], debug=args.debug)
    print(f"HF: {len(hf_stats)} forecasts with {season['label']} data")
    print(f"NWRFC: {len(nwrfc_stats)} forecasts with {season['label']} data")

    plot_volume_forecasts(hf_stats, nwrfc_stats, observed_by_label, pct_avg_by_label,
                           args.site_id, args.output,
                           season_label=season["label"],
                           historical_mean_maf=historical_mean_maf)


if __name__ == "__main__":
    main()
