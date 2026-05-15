"""
Natural Flow Comparison: BPA (2020 Level Modified Streamflow) vs NWRFC (Adjusted Runoff)
WY 2014–2018 (Oct 2013 – Sep 2018)

Stations:
  - The Dalles:   BPA TDA6M  vs NWRFC TDAO3W
  - Albeni Falls: BPA ALF6M  vs NWRFC ALFW1W  (proxy for Boundary Dam)
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── Config ────────────────────────────────────────────────────────────────────

BPA_DIR    = "/Users/hunterjohnson/Desktop/Claude Code/daily/daily"
OUTPUT_DIR = "/Users/hunterjohnson/Desktop/Claude Code/results"

WATER_YEARS   = [2014, 2015, 2016, 2017, 2018]
MONTHS        = ["OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP"]
CFS_DAY_TO_AF = 1.98347  # 1 cfs for 1 day = 1.98347 acre-feet

# ── Data Source Summary ───────────────────────────────────────────────────────

def print_data_summary():
    print("\n" + "="*90)
    print("  DATA SOURCES")
    print("="*90)
    print("""
  1. BPA — 2020 Level Modified Streamflow
     Report: https://www.bpa.gov/-/media/Aep/power/historical-streamflow-reports/2020-level-modified-streamflow.pdf
     Data:   https://www.bpa.gov/-/media/Aep/power/historical-streamflow-reports/historic-streamflow-all-daily-data.zip
     Resolution : Daily (cfs)
     Period     : WY 1929–2018 (Jul 1928–Sep 2018)
     Flow type  : Modified (M) — naturalized flow adjusted to 2020 irrigation depletion
                  levels; removes regulation effects, retains current-era consumptive use

  2. NWRFC — Columbia Basin Runoff Summary
     URL        : https://www.nwrfc.noaa.gov/runoff/runoff_summary.php
     Resolution : Monthly (KAF)
     Period     : WY 2014–present (Oct 2013–ongoing)
     Flow type  : Adjusted Runoff (W suffix) — naturalized flow with regulation removed
""")
    print("="*90)
    print("  STATION LOCATIONS & CODENAMES")
    print("="*90)
    header = f"  {'Dam':<16} {'River':<22} {'BPA Code':<10} {'BPA Period':<30} {'NWRFC Code':<12} {'NWRFC Period':<30} {'Comparison Window'}"
    print(header)
    print("  " + "-"*148)
    rows = [
        ("The Dalles",   "Columbia R., OR",    "TDA6M",  "WY 1929–2018 (Jul 1928–Sep 2018)", "TDAO3W",  "WY 2014–present (Oct 2013–ongoing)", "WY 2014–2018 (Oct 2013–Sep 2018)"),
        ("Albeni Falls", "Pend Oreille R., ID", "ALF6M",  "WY 1929–2018 (Jul 1928–Sep 2018)", "ALFW1W",  "WY 2014–present (Oct 2013–ongoing)", "WY 2014–2018 (Oct 2013–Sep 2018)"),
    ]
    for r in rows:
        print(f"  {r[0]:<16} {r[1]:<22} {r[2]:<10} {r[3]:<30} {r[4]:<12} {r[5]:<30} {r[6]}")
    print()
    print("  Note: Albeni Falls is the closest available NWRFC proxy for Boundary Dam.")
    print("        Comparison window is constrained by BPA dataset ending Sep 2018.")
    print("="*90 + "\n")


STATIONS = [
    {
        "name":       "The Dalles",
        "bpa_file":   "TDA6M_daily.xlsx",
        "nwrfc_id":   "TDAO3W",
        "slug":       "the_dalles",
    },
    {
        "name":       "Albeni Falls",
        "bpa_file":   "ALF6M_daily.xlsx",
        "nwrfc_id":   "ALFW1W",
        "slug":       "albeni_falls",
    },
]

MONTH_TO_WY_ORDER = {10:1, 11:2, 12:3, 1:4, 2:5, 3:6, 4:7, 5:8, 6:9, 7:10, 8:11, 9:12}
WY_ORDER_TO_MONTH = {v: k for k, v in MONTH_TO_WY_ORDER.items()}
MONTH_NAMES       = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                     7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
WY_COLORS         = {2014:"#1f77b4", 2015:"#ff7f0e", 2016:"#2ca02c", 2017:"#d62728", 2018:"#9467bd"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_bpa(filename):
    """Load a BPA daily xlsx, filter to WY 2014–2018, aggregate to monthly KAF."""
    path = f"{BPA_DIR}/{filename}"
    df = pd.read_excel(path)
    df.columns = ["date", "cfs"]
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= "2013-10-01") & (df["date"] <= "2018-09-30")].copy()
    df["wy"]    = df["date"].apply(lambda d: d.year + 1 if d.month >= 10 else d.year)
    df["month"] = df["date"].dt.month
    monthly = df.groupby(["wy", "month"])["cfs"].sum().reset_index()
    monthly["kaf_bpa"]  = monthly["cfs"] * CFS_DAY_TO_AF / 1000
    monthly["wy_month"] = monthly["month"].map(MONTH_TO_WY_ORDER)
    return monthly.sort_values(["wy", "wy_month"])


def scrape_nwrfc(wy, nwrfc_id):
    """
    Scrape monthly KAF for a given station from NWRFC runoff summary.
    Row structure: [RUNOFF, <ID>, OBS_DATE, WY, CURR, OCT..SEP, ...]
    Monthly values are at cells[5:17].
    """
    url = f"https://www.nwrfc.noaa.gov/runoff/runoff_summary.php?date=10/01/{wy}"
    r   = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if len(cells) >= 17 and cells[0] == "RUNOFF" and cells[1] == nwrfc_id:
            try:
                return {m: (float(cells[5 + k]) if cells[5 + k] not in ("", "-", "M") else None)
                        for k, m in enumerate(MONTHS)}
            except (ValueError, IndexError):
                return None
    return None


def build_nwrfc_df(nwrfc_id):
    """Scrape all water years for a station and return a tidy DataFrame."""
    records = []
    for wy in WATER_YEARS:
        data = scrape_nwrfc(wy, nwrfc_id)
        if data:
            for m_order, m_label in enumerate(MONTHS, start=1):
                records.append({
                    "wy":        wy,
                    "month":     WY_ORDER_TO_MONTH[m_order],
                    "wy_month":  m_order,
                    "kaf_nwrfc": data.get(m_label),
                })
            print(f"    WY {wy}: OK — OCT={data.get('OCT')}, APR={data.get('APR')}, SEP={data.get('SEP')}")
        else:
            print(f"    WY {wy}: FAILED")
    return pd.DataFrame(records)


def make_x_labels(df):
    return [f"Oct\nWY{int(r.wy)}" if r.wy_month == 1 else MONTH_NAMES[r.month][:3]
            for r in df.itertuples()]


# ── Main loop ─────────────────────────────────────────────────────────────────

all_results = {}

print_data_summary()

for stn in STATIONS:
    name      = stn["name"]
    nwrfc_id  = stn["nwrfc_id"]
    slug      = stn["slug"]

    print(f"\n{'='*60}")
    print(f"  {name}  |  BPA: {stn['bpa_file']}  |  NWRFC: {nwrfc_id}")
    print(f"{'='*60}")

    # 1. BPA
    print(f"  Loading BPA...")
    bpa = load_bpa(stn["bpa_file"])
    print(f"    {len(bpa)} monthly rows ({bpa['wy'].min()}–{bpa['wy'].max()})")

    # 2. NWRFC
    print(f"  Scraping NWRFC ({nwrfc_id})...")
    nwrfc = build_nwrfc_df(nwrfc_id)
    print(f"    {len(nwrfc)} monthly rows")

    # 3. Merge
    df = pd.merge(
        bpa[["wy", "month", "wy_month", "kaf_bpa"]],
        nwrfc[["wy", "month", "kaf_nwrfc"]],
        on=["wy", "month"], how="inner"
    )
    df["diff_kaf"] = df["kaf_nwrfc"] - df["kaf_bpa"]
    df["diff_pct"] = df["diff_kaf"] / df["kaf_bpa"] * 100
    df["month_name"] = df["month"].map(MONTH_NAMES)
    all_results[slug] = df

    # 4. Print summary
    print(f"\n  ── Summary Statistics ──────────────────────────────────")
    print(f"  {'Metric':<32} {'BPA (M)':>10} {'NWRFC':>10} {'Diff':>10}")
    print(f"  {'-'*64}")
    for wy in WATER_YEARS:
        sub = df[df["wy"] == wy]
        b, n = sub["kaf_bpa"].sum(), sub["kaf_nwrfc"].sum()
        print(f"  WY {wy} Annual Total (KAF)          {b:>10.0f} {n:>10.0f} {(n-b)/b*100:>+9.1f}%")
    print()
    ob, on = df["kaf_bpa"].sum(), df["kaf_nwrfc"].sum()
    print(f"  5-Year Total (KAF)              {ob:>10.0f} {on:>10.0f} {(on-ob)/ob*100:>+9.1f}%")
    print(f"  Mean Monthly Diff (KAF)         {'':>10} {df['diff_kaf'].mean():>10.0f}")
    print(f"  Mean Monthly Diff (%)           {'':>10} {df['diff_pct'].mean():>10.1f}%")
    print(f"  Monthly Correlation             {'':>10} {df['kaf_bpa'].corr(df['kaf_nwrfc']):>10.4f}")

    # 5. Save CSV
    df_out = df[["wy", "month_name", "kaf_bpa", "kaf_nwrfc", "diff_kaf", "diff_pct"]].copy()
    df_out.columns = ["Water Year", "Month", "BPA Modified (KAF)", "NWRFC Adjusted (KAF)", "Diff (KAF)", "Diff (%)"]
    csv_path = f"{OUTPUT_DIR}/{slug}_comparison.csv"
    df_out.to_csv(csv_path, index=False, float_format="%.1f")
    print(f"\n  Saved: {csv_path}")

    # 6. Plot
    wy_starts  = [i for i, r in enumerate(df.itertuples()) if r.wy_month == 1]
    x_labels   = make_x_labels(df)
    x          = range(len(df))

    fig, axes = plt.subplots(3, 1, figsize=(14, 14))
    fig.suptitle(
        f"{name} — Natural Flow Comparison: BPA (Modified) vs NWRFC (Adjusted)\n"
        f"WY 2014–2018 (Oct 2013 – Sep 2018)",
        fontsize=13
    )

    # Panel 1: time series
    ax1 = axes[0]
    ax1.plot(x, df["kaf_bpa"],   label="BPA Modified (M)",  color="#1f77b4", linewidth=1.8)
    ax1.plot(x, df["kaf_nwrfc"], label="NWRFC Adjusted (W)", color="#ff7f0e", linewidth=1.8, linestyle="--")
    for xs in wy_starts[1:]:
        ax1.axvline(xs - 0.5, color="gray", linewidth=0.8, linestyle=":")
    ax1.set_xticks(range(len(df)))
    ax1.set_xticklabels(x_labels, fontsize=7.5)
    ax1.set_ylabel("Monthly Volume (KAF)")
    ax1.set_title("Monthly Flow Volume")
    ax1.legend()
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax1.grid(axis="y", alpha=0.3)

    # Panel 2: difference bars
    ax2 = axes[1]
    bar_colors = ["#d62728" if v > 0 else "#2ca02c" for v in df["diff_kaf"]]
    ax2.bar(x, df["diff_kaf"], color=bar_colors, width=0.7)
    for xs in wy_starts[1:]:
        ax2.axvline(xs - 0.5, color="gray", linewidth=0.8, linestyle=":")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_xticks(range(len(df)))
    ax2.set_xticklabels(x_labels, fontsize=7.5)
    ax2.set_ylabel("Difference (KAF)\nNWRFC − BPA")
    ax2.set_title("Monthly Difference (red = NWRFC higher, green = BPA higher)")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:+,.0f}"))
    ax2.grid(axis="y", alpha=0.3)

    # Panel 3: scatter by WY
    ax3 = axes[2]
    for wy in WATER_YEARS:
        sub = df[df["wy"] == wy]
        ax3.scatter(sub["kaf_bpa"], sub["kaf_nwrfc"], label=f"WY {wy}",
                    color=WY_COLORS[wy], s=50, zorder=3)
    lims = [min(df["kaf_bpa"].min(), df["kaf_nwrfc"].min()) * 0.9,
            max(df["kaf_bpa"].max(), df["kaf_nwrfc"].max()) * 1.05]
    ax3.plot(lims, lims, "k--", linewidth=1, label="1:1 line", zorder=2)
    ax3.set_xlim(lims); ax3.set_ylim(lims)
    ax3.set_xlabel("BPA Modified (KAF)")
    ax3.set_ylabel("NWRFC Adjusted (KAF)")
    ax3.set_title("Scatter: BPA vs NWRFC Monthly Volumes")
    ax3.legend(ncol=3, fontsize=9)
    ax3.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax3.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax3.grid(alpha=0.3)

    plt.tight_layout()
    plot_path = f"{OUTPUT_DIR}/{slug}_comparison.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {plot_path}")

print("\n\nAll stations complete.")
