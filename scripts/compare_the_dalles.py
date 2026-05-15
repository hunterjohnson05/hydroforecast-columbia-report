"""
The Dalles Natural Flow Comparison: BPA vs NWRFC
WY 2014–2018 (Oct 2013 – Sep 2018)

BPA source:  TDA6NP_daily.xlsx  — daily cfs, Natural Period (NRNI)
NWRFC source: runoff_summary.php — monthly KAF, TDAO3W (adjusted natural flow)
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

BPA_FILE = "/Users/hunterjohnson/Desktop/Claude Code/daily/daily/TDA6NP_daily.xlsx"
CFS_DAY_TO_AF = 1.98347   # 1 cfs for 1 day = 1.98347 acre-feet
OUTPUT_DIR = "/Users/hunterjohnson/Desktop/Claude Code"

WATER_YEARS = [2014, 2015, 2016, 2017, 2018]
MONTHS = ["OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP"]

# ── 1. Load & aggregate BPA daily → monthly KAF ──────────────────────────────

print("Loading BPA TDA6NP daily data...")
df_bpa = pd.read_excel(BPA_FILE)
df_bpa.columns = ["date", "cfs"]
df_bpa["date"] = pd.to_datetime(df_bpa["date"])

# Filter to WY 2014–2018: Oct 1 2013 – Sep 30 2018
df_bpa = df_bpa[(df_bpa["date"] >= "2013-10-01") & (df_bpa["date"] <= "2018-09-30")].copy()

# Assign water year and calendar month
df_bpa["wy"] = df_bpa["date"].apply(lambda d: d.year + 1 if d.month >= 10 else d.year)
df_bpa["month"] = df_bpa["date"].dt.month

# Sum daily cfs → monthly KAF
df_bpa_monthly = (
    df_bpa.groupby(["wy", "month"])["cfs"]
    .sum()
    .reset_index()
)
df_bpa_monthly["kaf_bpa"] = df_bpa_monthly["cfs"] * CFS_DAY_TO_AF / 1000

# Map month number to WY month label (OCT=1st month of WY)
month_to_wy_order = {10: 1, 11: 2, 12: 3, 1: 4, 2: 5, 3: 6,
                      4: 7,  5: 8,  6: 9, 7: 10, 8: 11, 9: 12}
df_bpa_monthly["wy_month"] = df_bpa_monthly["month"].map(month_to_wy_order)
df_bpa_monthly = df_bpa_monthly.sort_values(["wy", "wy_month"])
print(f"  BPA rows: {len(df_bpa_monthly)} ({df_bpa_monthly['wy'].min()}–{df_bpa_monthly['wy'].max()})")


# ── 2. Scrape NWRFC monthly KAF for TDAO3W ───────────────────────────────────

def scrape_nwrfc_year(wy):
    """
    Query the NWRFC runoff summary for a completed water year.
    date=10/01/YYYY returns the WY that just ended (e.g. 10/01/2016 → WY 2016).
    Row structure: [RUNOFF, TDAO3W, OBS_DATE, WY, CURR, OCT, NOV, DEC, JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, ...]
    Monthly values are at cells[5:17].
    """
    url = f"https://www.nwrfc.noaa.gov/runoff/runoff_summary.php?date=10/01/{wy}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for row in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        # Target: cells[0]='RUNOFF', cells[1]='TDAO3W', cells[5:17] = OCT..SEP
        if len(cells) >= 17 and cells[0] == "RUNOFF" and cells[1] == "TDAO3W":
            try:
                return {m: (float(cells[5 + k]) if cells[5 + k] not in ("", "-", "M") else None)
                        for k, m in enumerate(MONTHS)}
            except (ValueError, IndexError):
                return None
    return None


print("\nScraping NWRFC for WY 2014–2018...")
nwrfc_records = []
for wy in WATER_YEARS:
    data = scrape_nwrfc_year(wy)
    if data:
        for m_order, m_label in enumerate(MONTHS, start=1):
            cal_month = [k for k, v in {10: 1, 11: 2, 12: 3, 1: 4, 2: 5, 3: 6,
                                         4: 7,  5: 8,  6: 9, 7: 10, 8: 11, 9: 12}.items()
                         if v == m_order][0]
            nwrfc_records.append({
                "wy": wy,
                "month": cal_month,
                "wy_month": m_order,
                "kaf_nwrfc": data.get(m_label)
            })
        print(f"  WY {wy}: OK — OCT={data.get('OCT')}, APR={data.get('APR')}, SEP={data.get('SEP')}")
    else:
        print(f"  WY {wy}: FAILED to parse TDAO3W row")

df_nwrfc = pd.DataFrame(nwrfc_records)
print(f"  NWRFC rows: {len(df_nwrfc)}")


# ── 3. Merge & compute differences ───────────────────────────────────────────

df = pd.merge(
    df_bpa_monthly[["wy", "month", "wy_month", "kaf_bpa"]],
    df_nwrfc[["wy", "month", "kaf_nwrfc"]],
    on=["wy", "month"],
    how="inner"
)
df["diff_kaf"] = df["kaf_nwrfc"] - df["kaf_bpa"]
df["diff_pct"] = (df["diff_kaf"] / df["kaf_bpa"]) * 100

print(f"\nMerged rows: {len(df)}")
print("\nSample (first 12 rows):")
print(df[["wy", "month", "kaf_bpa", "kaf_nwrfc", "diff_kaf", "diff_pct"]].head(12).to_string(index=False, float_format="%.1f"))


# ── 4. Summary stats ─────────────────────────────────────────────────────────

print("\n── Summary Statistics ─────────────────────────────────────")
print(f"{'Metric':<35} {'BPA':>10} {'NWRFC':>10} {'Diff %':>10}")
print("-" * 68)

for wy in WATER_YEARS:
    sub = df[df["wy"] == wy]
    bpa_ann = sub["kaf_bpa"].sum()
    nwrfc_ann = sub["kaf_nwrfc"].sum()
    pct = (nwrfc_ann - bpa_ann) / bpa_ann * 100
    print(f"  WY {wy} Annual Total (KAF)         {bpa_ann:>10.0f} {nwrfc_ann:>10.0f} {pct:>+9.1f}%")

print()
overall_bpa = df["kaf_bpa"].sum()
overall_nwrfc = df["kaf_nwrfc"].sum()
overall_pct = (overall_nwrfc - overall_bpa) / overall_bpa * 100
print(f"  5-Year Total (KAF)               {overall_bpa:>10.0f} {overall_nwrfc:>10.0f} {overall_pct:>+9.1f}%")
print(f"  Mean Monthly Diff (KAF)          {'':>10} {df['diff_kaf'].mean():>10.0f}")
print(f"  Mean Monthly Diff (%)            {'':>10} {df['diff_pct'].mean():>10.1f}%")
print(f"  Correlation (monthly)            {'':>10} {df['kaf_bpa'].corr(df['kaf_nwrfc']):>10.4f}")


# ── 5. Save comparison table to CSV ──────────────────────────────────────────

month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
               7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
df["month_name"] = df["month"].map(month_names)
df_out = df[["wy", "month_name", "kaf_bpa", "kaf_nwrfc", "diff_kaf", "diff_pct"]].copy()
df_out.columns = ["Water Year", "Month", "BPA NP (KAF)", "NWRFC Adj (KAF)", "Diff (KAF)", "Diff (%)"]
csv_path = f"{OUTPUT_DIR}/the_dalles_comparison.csv"
df_out.to_csv(csv_path, index=False, float_format="%.1f")
print(f"\nSaved: {csv_path}")


# ── 6. Plots ──────────────────────────────────────────────────────────────────

fig, axes = plt.subplots(3, 1, figsize=(14, 14))
fig.suptitle("The Dalles — Natural Flow Comparison: BPA (NP) vs NWRFC (Adjusted)\nWY 2014–2018", fontsize=14)

# ── Plot 1: Monthly time series ──
ax1 = axes[0]
x = range(len(df))
ax1.plot(x, df["kaf_bpa"],   label="BPA Natural Period", color="#1f77b4", linewidth=1.8)
ax1.plot(x, df["kaf_nwrfc"], label="NWRFC Adjusted",     color="#ff7f0e", linewidth=1.8, linestyle="--")

# WY boundary lines
wy_starts = [i for i, (_, row) in enumerate(df.iterrows()) if row["wy_month"] == 1]
for xs in wy_starts[1:]:
    ax1.axvline(xs - 0.5, color="gray", linewidth=0.8, linestyle=":")

# X-axis labels: abbreviated month + WY change marker
tick_labels = []
for _, row in df.iterrows():
    tick_labels.append(row["month_name"][:1] if row["wy_month"] != 1 else f"\nWY{int(row['wy'])}")
ax1.set_xticks(range(len(df)))
ax1.set_xticklabels([m["month_name"][:3] if m["wy_month"] != 1 else f"Oct\nWY{int(m['wy'])}"
                     for _, m in df.iterrows()], fontsize=7.5)
ax1.set_ylabel("Monthly Volume (KAF)")
ax1.set_title("Monthly Flow Volume")
ax1.legend()
ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
ax1.grid(axis="y", alpha=0.3)

# ── Plot 2: Difference (NWRFC − BPA) ──
ax2 = axes[1]
colors = ["#d62728" if v > 0 else "#2ca02c" for v in df["diff_kaf"]]
ax2.bar(range(len(df)), df["diff_kaf"], color=colors, width=0.7)
for xs in wy_starts[1:]:
    ax2.axvline(xs - 0.5, color="gray", linewidth=0.8, linestyle=":")
ax2.axhline(0, color="black", linewidth=0.8)
ax2.set_xticks(range(len(df)))
ax2.set_xticklabels([m["month_name"][:3] if m["wy_month"] != 1 else f"Oct\nWY{int(m['wy'])}"
                     for _, m in df.iterrows()], fontsize=7.5)
ax2.set_ylabel("Difference (KAF)\nNWRFC − BPA")
ax2.set_title("Monthly Difference (positive = NWRFC higher)")
ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:+,.0f}"))
ax2.grid(axis="y", alpha=0.3)

# ── Plot 3: Scatter ──
ax3 = axes[2]
wy_colors = {2014:"#1f77b4", 2015:"#ff7f0e", 2016:"#2ca02c", 2017:"#d62728", 2018:"#9467bd"}
for wy in WATER_YEARS:
    sub = df[df["wy"] == wy]
    ax3.scatter(sub["kaf_bpa"], sub["kaf_nwrfc"], label=f"WY {wy}",
                color=wy_colors[wy], s=50, zorder=3)

# 1:1 line
lims = [min(df["kaf_bpa"].min(), df["kaf_nwrfc"].min()) * 0.9,
        max(df["kaf_bpa"].max(), df["kaf_nwrfc"].max()) * 1.05]
ax3.plot(lims, lims, "k--", linewidth=1, label="1:1 line", zorder=2)
ax3.set_xlim(lims); ax3.set_ylim(lims)
ax3.set_xlabel("BPA Natural Period (KAF)")
ax3.set_ylabel("NWRFC Adjusted (KAF)")
ax3.set_title("Scatter: BPA vs NWRFC Monthly Volumes")
ax3.legend(ncol=3, fontsize=9)
ax3.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
ax3.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
ax3.grid(alpha=0.3)

plt.tight_layout()
plot_path = f"{OUTPUT_DIR}/the_dalles_comparison.png"
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
print(f"Saved: {plot_path}")
plt.show()
print("\nDone.")
