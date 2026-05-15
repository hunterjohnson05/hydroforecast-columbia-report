"""
generate_viz.py
---------------
Builds an interactive HTML visualization from runoff.db and writes it to
    daily_results/timeseries.html

The file is overwritten each day so there is always one up-to-date file to open
or share. Two views are included:

  Tab 1 — Time Series
    • Cumulative KAF (RUNOFF solid, 30-yr AVERAGE dashed) per site over time
    • % of Average per site over time
    • Dropdown to switch between Oct-to-date / Jan-to-date / Apr-to-date

  Tab 2 — Latest Snapshot
    • Horizontal bar chart of every site's current cumulative KAF vs average
    • Sorted by % of average so outliers stand out

Run standalone:
    python3 generate_viz.py
Or called automatically by run_daily.py after each scrape.
"""

import os
import sys
import sqlite3
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_PATH, DAILY_RESULTS_DIR

# ---------------------------------------------------------------------------
# Watershed groupings  (HB5 IDs → group name)
# ---------------------------------------------------------------------------
WATERSHED_GROUPS = {
    "Upper Columbia (CA)":      ["MCDQ2W","REVQ2W","ARDQ2W","DCDQ2W","QBYQ2W",
                                  "SLCQ2W","BIRQ2W","SMKQ2W","NITW1W","CIBW1W"],
    "Clark Fork / Flathead":    ["LYDM8W","LEOI1W","BFEI1W","ABBM8W","ABOM8W",
                                  "DARM8W","BITM8W","BELM8W","SRGM8W","FCFM8W",
                                  "WGCM8W","HHWM8W","CFMM8W","KERM8W","PLNM8W",
                                  "CABI1W","BONM8W"],
    "Spokane / Pend Oreille":   ["PRTI1W","ALFW1W","ENVI1W","CLDI1W","COEI1W",
                                  "SPOW1W","LLKW1W","LAUW1W"],
    "Main Columbia":            ["GCDW1W","GCDW1","TONW1W","PATW1W","STHW1W",
                                  "CHDW1W","PESW1W","RISW1W","PRDW1W","KEEW1W",
                                  "MCDW1W","MCDW1","JDAO3W","JDAO3","TDAO3W",
                                  "TDAO3","BONO3W","BONO3"],
    "Yakima":                   ["KACW1W","CLEW1W","HLKW1W","BUMW1W","RIMW1W",
                                  "NACW1W","PARW1W","KIOW1W"],
    "Upper Snake":              ["JLKW4W","SALW4W","GREW4W","PALI1W","HEII1W",
                                  "ANTI1W","TEAI1W","REXI1W","REXI1","RIRI1W",
                                  "SHYI1W","CHEI1W"],
    "Lower Snake / Boise":      ["TOPI1W","AMFI1W","MILI1W","HWRI1W","MACI1W",
                                  "HALI1W","MAGI1W","WODI1W","SKHI1W","HOTI1W",
                                  "SWAI1W","WDHN2W","OWYO3W","OWYO3","ARAI1W",
                                  "LUCI1W","PARI1W","MADO3W","BEUO3W","DRBI1W",
                                  "CSCI1W","HRSI1W","EMMI1W","WSRI1W","WEII1W",
                                  "UNYO3W","BRNI1W","HCDI1W","SFLN2W"],
    "Clearwater / Salmon":      ["IMNO3W","SMNI1W","WHBI1W","LGNO3W","TRYO3W",
                                  "LSTO3W","ORFI1W","DWRI1W","SPDI1W","LGDW1W",
                                  "EASI1W"],
    "Lower Columbia / Willamette": ["VIDO3W","MEHO3W","WTLO3W","SLMO3W","ESTO3W",
                                    "MEWW1W","MYDW1W","CASW1W","GIBO3W","PDTO3W",
                                    "MONO3W","SERO3W","MODO3W","RYGO3W","DRSW1W",
                                    "CONW1W"],
    "OR High Desert":             ["BUSO3W","DONO3W","EGCO3W"],
}

# Plotly qualitative palette (one colour per watershed group)
PALETTE = [
    "#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
    "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf",
]

def site_to_group(site_id: str) -> str:
    for group, ids in WATERSHED_GROUPS.items():
        if site_id in ids:
            return group
    return "Other"

def site_to_color(site_id: str) -> str:
    groups = list(WATERSHED_GROUPS.keys())
    g = site_to_group(site_id)
    idx = groups.index(g) if g in groups else len(groups) - 1
    return PALETTE[idx % len(PALETTE)]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)

    runoff = pd.read_sql("""
        SELECT site_id, site_name, obs_date, row_type,
               cumul_oct_to_date, cumul_jan_to_date, cumul_apr_to_date
        FROM runoff_observations
        WHERE row_type IN ('RUNOFF', 'AVERAGE', 'PCT_AVG')
        ORDER BY obs_date, site_id
    """, conn)

    monthly = pd.read_sql("""
        SELECT site_id, site_name, obs_date, row_type,
               oct, nov, dec, jan, feb, mar, apr, may, jun, jul, aug, sep
        FROM runoff_observations
        WHERE row_type = 'RUNOFF'
        ORDER BY obs_date DESC, site_id
    """, conn)

    conn.close()
    return runoff, monthly


# ---------------------------------------------------------------------------
# Tab 1: % of Average time series (one line per site, coloured by watershed)
# ---------------------------------------------------------------------------
def _dup_names(df: pd.DataFrame) -> set:
    """Return the set of site_names that appear under more than one site_id."""
    counts = (
        df[["site_id","site_name"]].drop_duplicates("site_id")
        ["site_name"].value_counts()
    )
    return set(counts[counts > 1].index)


def _disp(site_id: str, site_name: str, dups: set) -> str:
    """Append (site_id) to disambiguate names that belong to multiple sites."""
    return f"{site_name} ({site_id})" if site_name in dups else site_name


def build_timeseries_fig(df: pd.DataFrame) -> tuple[go.Figure, list]:
    """
    Single panel: % of 30-yr average over time for every site.
    Dropdown switches between Oct-to-date / Jan-to-date / Apr-to-date.
    """

    periods = [
        ("Oct 1 → Today",  "cumul_oct_to_date"),
        ("Jan 1 → Today",  "cumul_jan_to_date"),
        ("Apr 1 → Today",  "cumul_apr_to_date"),
    ]

    fig = go.Figure()

    # Sort sites by watershed group then name so the legend is organised
    all_sites = (
        df[df["row_type"] == "PCT_AVG"][["site_id","site_name"]]
        .drop_duplicates("site_id")
        .copy()
    )
    all_sites["group"] = all_sites["site_id"].map(site_to_group)
    group_order = list(WATERSHED_GROUPS.keys())
    all_sites["g_order"] = all_sites["group"].map(
        lambda g: group_order.index(g) if g in group_order else len(group_order)
    )
    all_sites = all_sites.sort_values(["g_order","site_name"]).reset_index(drop=True)

    # Sites whose display name needs the site_id appended to disambiguate
    dups = _dup_names(df)

    sites     = all_sites["site_id"].tolist()
    n_periods = len(periods)
    n_sites   = len(sites)

    # trace_info: one entry per trace added, used by JS for search + period switching
    trace_info: list[dict] = []

    for p_idx, (period_label, col) in enumerate(periods):
        default_vis = "legendonly" if p_idx == 0 else False

        for _, site_row in all_sites.iterrows():
            site_id   = site_row["site_id"]
            site_name = site_row["site_name"]
            group     = site_row["group"]
            label     = _disp(site_id, site_name, dups)

            site_df = df[df["site_id"] == site_id]
            pct_df  = site_df[site_df["row_type"] == "PCT_AVG"].sort_values("obs_date")

            if pct_df.empty or pct_df[col].isna().all():
                continue

            color = site_to_color(site_id)

            fig.add_trace(go.Scatter(
                x=pct_df["obs_date"],
                y=pct_df[col],
                mode="lines+markers",
                name=label,
                legendgrouptitle_text=group if p_idx == 0 else None,
                legendgroup=group,
                line=dict(color=color, width=1.8),
                marker=dict(size=5),
                showlegend=(p_idx == 0),
                visible=default_vis,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "%{x}<br>%{y:.1f}% of avg<extra></extra>"
                ),
            ))
            trace_info.append({"name": label, "id": site_id,
                                "group": group, "period": p_idx})

    # 100% reference line — always visible, not searchable
    all_dates = sorted(df["obs_date"].unique())
    fig.add_trace(go.Scatter(
        x=all_dates, y=[100] * len(all_dates),
        mode="lines", name="100% (avg)",
        line=dict(color="black", width=1.2, dash="dot"),
        legendgroup="reference",
        showlegend=True,
        visible=True,
        hoverinfo="skip",
    ))
    trace_info.append({"name": "100% (avg)", "id": "__ref__",
                        "group": "__ref__", "period": -1})

    fig.update_layout(
        yaxis_title="% of 30-yr Average",
        xaxis_title="Observation Date",
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(gridcolor="#eeeeee", zeroline=False),
        xaxis=dict(gridcolor="#eeeeee"),
        height=540,
        margin=dict(t=20, r=20, b=60, l=60),
        legend=dict(groupclick="toggleitem"),   # click group title = toggle all; click item = toggle one
    )

    return fig, trace_info


# ---------------------------------------------------------------------------
# Tab 2: Latest snapshot bar chart
# ---------------------------------------------------------------------------
def _pct_color(v: float) -> str:
    if v < 75:   return "#d62728"
    if v < 90:   return "#ff7f0e"
    if v <= 115: return "#2ca02c"
    return "#1f77b4"


def build_snapshot_fig(df: pd.DataFrame) -> tuple[go.Figure, list, str]:
    latest_date = df["obs_date"].max()
    latest      = df[df["obs_date"] == latest_date]

    run = latest[latest["row_type"] == "RUNOFF"][
        ["site_id","site_name","cumul_oct_to_date","cumul_jan_to_date","cumul_apr_to_date"]
    ].copy()
    pct = latest[latest["row_type"] == "PCT_AVG"][
        ["site_id","cumul_oct_to_date"]
    ].rename(columns={"cumul_oct_to_date": "pct_oct"})

    snap = run.merge(pct, on="site_id", how="left").dropna(subset=["pct_oct"])

    # Sort by watershed group (same order as time series), then pct within group
    group_order = list(WATERSHED_GROUPS.keys())
    snap["group"]   = snap["site_id"].map(site_to_group)
    snap["g_order"] = snap["group"].map(
        lambda g: group_order.index(g) if g in group_order else len(group_order)
    )
    snap = snap.sort_values(["g_order", "pct_oct"], ascending=[True, True]).reset_index(drop=True)

    # Disambiguate site names that appear under multiple site_ids (e.g. TDAO3 vs TDAO3W)
    dups = _dup_names(df)

    # Full dataset for JS-driven search/redraw — includes group for divider rendering
    snap_data = [
        {
            "id":    row["site_id"],
            "name":  _disp(row["site_id"], row["site_name"], dups),
            "pct":   round(row["pct_oct"], 1),
            "color": _pct_color(row["pct_oct"]),
            "group": row["group"],
        }
        for _, row in snap.iterrows()
    ]

    x_max = max(snap["pct_oct"].max() * 1.12, 130)
    labels = [_disp(r["site_id"], r["site_name"], dups) for _, r in snap.iterrows()]

    fig = go.Figure(go.Bar(
        x=snap["pct_oct"],
        y=labels,
        orientation="h",
        marker_color=[_pct_color(v) for v in snap["pct_oct"]],
        text=[f"{v:.0f}%" for v in snap["pct_oct"]],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>% of avg (Oct→today): %{x:.1f}%<extra></extra>",
    ))

    fig.add_vline(x=100, line_width=1.5, line_dash="dash", line_color="black")

    fig.update_layout(
        title=f"% of 30-yr Average — Oct-to-Date  ({latest_date})",
        xaxis_title="% of Average",
        xaxis=dict(range=[0, x_max]),
        height=max(600, len(snap) * 18),
        margin=dict(l=320, r=80, t=60, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_yaxes(tickfont=dict(size=11))

    return fig, snap_data, latest_date


# ---------------------------------------------------------------------------
# Assemble full HTML
# ---------------------------------------------------------------------------
def generate(output_path: str | None = None) -> str:
    os.makedirs(DAILY_RESULTS_DIR, exist_ok=True)
    if output_path is None:
        output_path = os.path.join(DAILY_RESULTS_DIR, "timeseries.html")

    df_cumul, _ = load_data()

    latest_date = df_cumul["obs_date"].max()
    n_days      = df_cumul["obs_date"].nunique()
    n_sites     = df_cumul["site_id"].nunique()
    generated   = datetime.now().strftime("%Y-%m-%d %H:%M")

    ts_fig,   ts_trace_info            = build_timeseries_fig(df_cumul)
    snap_fig, snap_data, snap_date     = build_snapshot_fig(df_cumul)

    ts_html   = ts_fig.to_html(full_html=False, include_plotlyjs="cdn",
                                div_id="ts-chart", config={"responsive": True})
    snap_html = snap_fig.to_html(full_html=False, include_plotlyjs=False,
                                  div_id="snap-chart", config={"responsive": True})

    ts_trace_json = json.dumps(ts_trace_info)
    snap_data_json = json.dumps(snap_data)
    n_periods = 3

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NWRFC Runoff — Columbia Basin</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f5f5f5; color: #333; }}
    header {{ background: #1a3a5c; color: white; padding: 18px 32px; }}
    header h1 {{ font-size: 1.4rem; font-weight: 600; }}
    header p  {{ font-size: 0.85rem; opacity: 0.8; margin-top: 4px; }}
    .tabs {{ display: flex; gap: 0; padding: 0 32px;
             background: #1a3a5c; border-bottom: 3px solid #2e6da4; }}
    .tab  {{ padding: 10px 24px; cursor: pointer; color: rgba(255,255,255,0.7);
             font-size: 0.9rem; border-bottom: 3px solid transparent;
             margin-bottom: -3px; transition: all 0.15s; }}
    .tab:hover   {{ color: white; }}
    .tab.active  {{ color: white; border-bottom-color: #f0a500; font-weight: 600; }}
    .panel       {{ display: none; padding: 24px 32px; }}
    .panel.active{{ display: block; }}
    .controls    {{ display: flex; align-items: center; gap: 12px;
                    margin-bottom: 10px; flex-wrap: wrap; }}
    .search-box  {{ padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px;
                    font-size: 0.88rem; width: 260px; outline: none; }}
    .search-box:focus {{ border-color: #2e6da4; box-shadow: 0 0 0 2px rgba(46,109,164,0.2); }}
    .period-btn  {{ padding: 5px 14px; border: 1px solid #ccc; border-radius: 4px;
                    background: white; cursor: pointer; font-size: 0.85rem;
                    transition: all 0.15s; }}
    .period-btn:hover  {{ border-color: #2e6da4; color: #2e6da4; }}
    .period-btn.active {{ background: #2e6da4; color: white; border-color: #2e6da4; }}
    .clear-btn   {{ padding: 5px 10px; border: 1px solid #ccc; border-radius: 4px;
                    background: white; cursor: pointer; font-size: 0.82rem; color: #666; }}
    .clear-btn:hover {{ border-color: #999; color: #333; }}
    .result-count{{ font-size: 0.78rem; color: #888; }}
    .legend-note {{ font-size: 0.78rem; color: #666; margin-bottom: 8px; }}
    .legend-note span {{ display: inline-block; width: 28px; height: 3px;
                         vertical-align: middle; margin-right: 4px; }}
    .chart-wrap  {{ background: white; border-radius: 6px;
                    box-shadow: 0 1px 4px rgba(0,0,0,0.1); padding: 16px; }}
    footer {{ text-align: center; font-size: 0.75rem; color: #999; padding: 20px; }}
  </style>
</head>
<body>

<header>
  <h1>NWRFC Columbia Basin — Runoff Tracker</h1>
  <p>Latest obs date: <strong>{latest_date}</strong> &nbsp;·&nbsp;
     {n_days} day{"s" if n_days != 1 else ""} on file &nbsp;·&nbsp;
     {n_sites} sites &nbsp;·&nbsp; Generated {generated}</p>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab(0)">📈 Cumulative Time Series</div>
  <div class="tab"        onclick="switchTab(1)">📊 Latest Snapshot</div>
</div>

<!-- ── Tab 0: Time Series ─────────────────────────────────────── -->
<div id="panel-0" class="panel active">
  <div class="controls">
    <input id="ts-search" class="search-box" type="text"
           placeholder="🔍 Search sites (e.g. Columbia, Snake, Mica)…"
           oninput="onTsSearch(this.value)">
    <button class="clear-btn" onclick="clearTsSearch()">✕ Clear</button>
    <span id="ts-count" class="result-count"></span>
    &nbsp;|&nbsp;
    <strong style="font-size:0.85rem">Period:</strong>
    <button class="period-btn active" onclick="setTsPeriod(0)">Oct 1 → Today</button>
    <button class="period-btn"        onclick="setTsPeriod(1)">Jan 1 → Today</button>
    <button class="period-btn"        onclick="setTsPeriod(2)">Apr 1 → Today</button>
  </div>
  <p class="legend-note">
    Each line = one site, coloured by watershed group &nbsp;·&nbsp;
    Search shows matching sites on the chart &nbsp;·&nbsp;
    Click a site in the legend to toggle it individually
  </p>
  <div class="chart-wrap">{ts_html}</div>
</div>

<!-- ── Tab 1: Snapshot ───────────────────────────────────────── -->
<div id="panel-1" class="panel">
  <div class="controls">
    <input id="snap-search" class="search-box" type="text"
           placeholder="🔍 Search sites (e.g. Columbia, Snake, Mica)…"
           oninput="onSnapSearch(this.value)">
    <button class="clear-btn" onclick="clearSnapSearch()">✕ Clear</button>
    <span id="snap-count" class="result-count"></span>
  </div>
  <p class="legend-note">
    Colour scale: &nbsp;
    <span style="background:#d62728;"></span>&lt;75% &nbsp;
    <span style="background:#ff7f0e;"></span>75–90% &nbsp;
    <span style="background:#2ca02c;"></span>90–115% &nbsp;
    <span style="background:#1f77b4;"></span>&gt;115% of average
  </p>
  <div class="chart-wrap">{snap_html}</div>
</div>

<footer>
  Data source: NOAA Northwest River Forecast Center —
  <a href="https://www.nwrfc.noaa.gov/runoff/runoff_summary.php" target="_blank">
  nwrfc.noaa.gov</a> &nbsp;·&nbsp; Units: KAF (Thousand Acre-Feet)
</footer>

<script>
// ── Shared ────────────────────────────────────────────────────────────────
function switchTab(idx) {{
  document.querySelectorAll(".tab").forEach((t,i)   => t.classList.toggle("active", i===idx));
  document.querySelectorAll(".panel").forEach((p,i) => p.classList.toggle("active", i===idx));
}}

// ── Time Series ───────────────────────────────────────────────────────────
const TS_TRACES   = {ts_trace_json};
const TS_NPERIODS = {n_periods};
let tsPeriod = 0;
let tsQuery  = "";

function renderTs() {{
  const q = tsQuery.trim().toLowerCase();
  const vis = TS_TRACES.map(t => {{
    if (t.period === -1) return true;              // reference line always on
    if (t.period !== tsPeriod) return false;       // wrong period: fully hidden
    if (q === "") return "legendonly";             // no search: legend only
    const match = t.name.toLowerCase().includes(q) || t.id.toLowerCase().includes(q);
    return match ? true : "legendonly";            // match: show on chart; else legend only
  }});
  Plotly.restyle("ts-chart", {{visible: vis}});

  // Update result count
  const n = q ? TS_TRACES.filter(t => t.period === tsPeriod && t.period !== -1 &&
    (t.name.toLowerCase().includes(q) || t.id.toLowerCase().includes(q))).length : "";
  document.getElementById("ts-count").textContent = q ? n + " site" + (n===1?"":"s") + " matched" : "";
}}

function setTsPeriod(p) {{
  tsPeriod = p;
  document.querySelectorAll(".period-btn").forEach((b,i) => b.classList.toggle("active", i===p));
  renderTs();
}}

function onTsSearch(val) {{ tsQuery = val; renderTs(); }}
function clearTsSearch()  {{ document.getElementById("ts-search").value = ""; tsQuery = ""; renderTs(); }}

// ── Snapshot ──────────────────────────────────────────────────────────────
const SNAP_ALL = {snap_data_json};
let snapQuery  = "";

function renderSnap() {{
  const q = snapQuery.trim().toLowerCase();
  const filtered = q
    ? SNAP_ALL.filter(d => d.name.toLowerCase().includes(q) || d.id.toLowerCase().includes(q))
    : SNAP_ALL;

  // Insert a zero-width divider row at every watershed group boundary.
  // Dividers appear as group-name labels on the y-axis with no visible bar.
  const rows = [];
  let curGroup = null;
  filtered.forEach(d => {{
    if (d.group !== curGroup) {{
      rows.push({{ __div: true, name: d.group, pct: 0 }});
      curGroup = d.group;
    }}
    rows.push(d);
  }});

  const gd  = document.getElementById("snap-chart");
  const pts  = filtered.length;
  const xmax = pts > 0 ? Math.max(...filtered.map(d => d.pct)) * 1.12 : 130;

  Plotly.react("snap-chart",
    [{{
      type: "bar", orientation: "h",
      x:    rows.map(r => r.__div ? 0 : r.pct),
      y:    rows.map(r => r.__div ? `▸ ${{r.name}}` : r.name),
      marker: {{
        color: rows.map(r => r.__div ? "rgba(0,0,0,0)" : r.color),
      }},
      text:         rows.map(r => r.__div ? "" : r.pct.toFixed(0) + "%"),
      textposition: "outside",
      // Per-point hovertemplate: skip dividers, show info for real bars
      hovertemplate: rows.map(r =>
        r.__div
          ? "<extra></extra>"
          : "<b>%{{y}}</b><br>% of avg (Oct→today): %{{x:.1f}}%<extra></extra>"
      ),
    }}],
    Object.assign({{}}, gd.layout, {{
      height: Math.max(400, rows.length * 18 + 100),
      xaxis:  Object.assign({{}}, gd.layout.xaxis, {{range: [0, Math.max(xmax, 130)]}}),
      shapes: [{{type:"line", x0:100, x1:100, y0:0, y1:1, yref:"paper",
                 line:{{color:"black", width:1.5, dash:"dash"}}}}],
    }})
  );

  document.getElementById("snap-count").textContent =
    q ? pts + " site" + (pts===1?"":"s") + " matched" : pts + " sites";
}}

function onSnapSearch(val) {{ snapQuery = val; renderSnap(); }}
function clearSnapSearch()  {{
  document.getElementById("snap-search").value = "";
  snapQuery = "";
  renderSnap();
}}

// Initialise snapshot count on load
window.addEventListener("load", () => renderSnap());
</script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)

    return output_path


if __name__ == "__main__":
    path = generate()
    print(f"Visualization written: {path}")
