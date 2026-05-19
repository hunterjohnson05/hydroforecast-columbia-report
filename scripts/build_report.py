#!/usr/bin/env python3
"""
build_report.py
---------------
Assembles the weekly HydroForecast vs NWRFC report from the latest plot outputs.
Embeds charts as base64 PNGs in a single self-contained HTML file.

Usage:
    python3 build_report.py
"""

import argparse
import base64
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lta import parse_season  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "results"
TODAY = date.today()

MONTH_TITLES = {
    "jan": "January", "feb": "February", "mar": "March",
    "apr": "April",   "may": "May",      "jun": "June",
    "jul": "July",    "aug": "August",   "sep": "September",
    "oct": "October", "nov": "November", "dec": "December",
}
MONTH_ORDER = list(MONTH_TITLES.keys())


def bar_chart_sections() -> list[dict]:
    """
    Auto-discover bar charts in the bar_chart subfolder.
    Returns one section dict per file; "Apr 1 → Today" always comes first,
    followed by monthly init charts sorted chronologically.
    Each dict includes 'tab_key' and 'tab_label' for the tab UI.
    """
    folder = RESULTS_DIR / "bar_chart"
    sections = []

    # "Apr 1 → Today" tab — always first if present
    apr1_path = folder / "the_dalles_apr1_season_init.png"
    if apr1_path.exists():
        sections.append({
            "title": "Cumulative Volume Comparison — The Dalles (Apr 1 Init, Season to Date)",
            "description": (
                "Grouped bar chart comparing observed (NWRFC), HydroForecast, and RFC cumulative "
                "volumes at The Dalles from the April 1 initialization through today. "
                "Each bar group is a weekly snapshot (7th, 14th, 21st, 28th of each month); "
                "percent errors are labeled relative to observed."
            ),
            "img_path": apr1_path,
            "tab_key":  "apr1-season",
            "tab_label": "Apr 1 → Today",
        })

    # Monthly init charts (Apr, May, …) — exclude the apr1_season file
    for png in sorted(
        (p for p in folder.glob("*_init.png") if "apr1_season" not in p.name),
        key=lambda p: MONTH_ORDER.index(p.stem.split("_")[-2])
                      if p.stem.split("_")[-2] in MONTH_ORDER else 99,
    ):
        month_key  = png.stem.split("_")[-2]
        month_name = MONTH_TITLES.get(month_key, month_key.capitalize())
        sections.append({
            "title": f"Cumulative Volume Comparison — The Dalles ({month_name} 1 Init)",
            "description": (
                f"Grouped bar chart comparing observed (NWRFC), HydroForecast, and RFC cumulative "
                f"volumes at The Dalles from the {month_name} 1 initialization onward. "
                f"Each bar group is a weekly snapshot; percent errors are labeled relative to observed."
            ),
            "img_path":  png,
            "tab_key":   month_key,
            "tab_label": month_name,
        })
    return sections


def evolution_tabs(season_label: str, season_slug: str) -> list[dict]:
    """Tab specs for the Apr–Aug / Apr–Sep forecast evolution section.
    Each dict includes a per-tab 'suffix_filter' used to pick the right PNG
    from the shared subfolder, and a 'chart_kind' for sources attribution.
    """
    return [
        {
            "tab_id":       "tab-evo-14",
            "tab_label":    "14-Day Outlook",
            "description": (
                f"Boxplot showing how HydroForecast and NWRFC ESP Natural {season_label} total volume "
                f"forecasts have evolved over the past 14 initialization dates. The lower panel "
                f"shows observed cumulative volume to date as a percentage of normal."
            ),
            "subfolder":     "volume_forecast_plot",
            "suffix_filter": season_slug,            # e.g. "apr_aug" — unique subfolder, no conflict
            "chart_kind":    "boxplot_14d",
        },
        {
            "tab_id":       "tab-evo-28",
            "tab_label":    "28-Day Outlook",
            "description": (
                f"Bars show the {season_label} seasonal volume forecast (MAF) for HydroForecast and NWRFC "
                f"ESP Natural over the past 28 daily initializations, with each value computed as "
                f"observed Apr-to-date plus the forecast remainder. Lines show the same as a "
                f"percentage of the long-term average (LTA = NWRFC's published 1991-2020 30-year "
                f"normal; same value used in the 14-Day Outlook tab), with the dashed reference at 100%."
            ),
            "subfolder":     "apr_aug_evolution",
            "suffix_filter": f"{season_slug}_evolution",  # e.g. "apr_aug_evolution"
            "chart_kind":    "evolution_28d",
        },
        {
            "tab_id":       "tab-evo-s2d",
            "tab_label":    "Apr 1 → Today",
            "description": (
                f"Same dual-axis style as the 28-Day Outlook, but the x-axis runs from April 1 of "
                f"the current year through today (capped at September 30). Bar width and label "
                f"cadence scale dynamically as the season progresses. Each bar represents the "
                f"forecast issued on that date, expressed as both MAF and % of the {season_label} "
                f"long-term average."
            ),
            "subfolder":     "apr_aug_evolution",
            "suffix_filter": f"apr1_to_date_{season_slug}",  # e.g. "apr1_to_date_apr_aug"
            "chart_kind":    "evolution_28d",
        },
    ]


def latest_png(subfolder: str, suffix_filter: str | None = None) -> Path | None:
    """
    Return the most recent .png in `subfolder`.
    If `suffix_filter` is given, only consider files whose name contains that token
    (e.g. "apr_aug" or "apr_sep"). Falls back to the most recent file in the folder
    if nothing matches the filter — this lets old (un-tagged) outputs still appear.
    """
    folder = RESULTS_DIR / subfolder
    if not folder.exists():
        return None
    if suffix_filter:
        pngs = sorted(p for p in folder.glob("*.png") if suffix_filter in p.name)
        if pngs:
            return pngs[-1]
    pngs = sorted(folder.glob("*.png"))
    return pngs[-1] if pngs else None


def embed_png(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'data:image/png;base64,{data}'


# Source attribution per chart type. The user gets the actual URLs / API endpoints
# data flowed from rather than just a generated PNG filename.
HF_API_SOURCE  = ('HydroForecast (HF)',
                   'Upstream Tech API — source <code>hydroforecast-seasonal</code> '
                   '(blended ERA5+GEFS mean)')
RFC_API_SOURCE = ('NWRFC ESP', 'Upstream Tech API — source <code>nwrfc-esp-natural</code>')
NWRFC_RUNOFF_LIVE = ('Observed cumulative volume + % of normal',
                      'Live HTTP fetch from '
                      '<a href="https://www.nwrfc.noaa.gov/runoff/runoff_summary.php" '
                      'target="_blank" rel="noopener">NWRFC runoff summary</a> '
                      '(one request per forecast init date)')
NWRFC_RUNOFF_DB = ('Observed Apr-to-date',
                    'Local <code>runoff.db</code> — populated daily by the scraper from '
                    '<a href="https://www.nwrfc.noaa.gov/runoff/runoff_summary.php" '
                    'target="_blank" rel="noopener">NWRFC runoff summary</a>')
LTA_SOURCE = ('Long-term average (1991-2020 30-year normal)',
               'Cached in <code>lta_normals</code> table from '
               '<a href="https://www.nwrfc.noaa.gov/natural/plot/nat_forecasts.php?id=TDAO3W" '
               'target="_blank" rel="noopener">NWRFC natural forecasts</a>')


def sources_html(chart_kind: str) -> str:
    """Return a 'Data sources' block listing upstream origins for a chart."""
    if chart_kind == "boxplot_14d":
        items = [HF_API_SOURCE, RFC_API_SOURCE, NWRFC_RUNOFF_LIVE, LTA_SOURCE]
    elif chart_kind == "evolution_28d":
        items = [HF_API_SOURCE, RFC_API_SOURCE, NWRFC_RUNOFF_DB, LTA_SOURCE]
    elif chart_kind == "bar_chart":
        items = [HF_API_SOURCE, RFC_API_SOURCE, NWRFC_RUNOFF_DB]
    elif chart_kind == "qq_lead_time":
        items = [
            HF_API_SOURCE,
            RFC_API_SOURCE,
            ("Observed daily flow",
             "Local <code>runoff.db</code> · <code>daily_kaf</code> column on RUNOFF "
             "rows for site TDAO3W (natural/unregulated), converted KAF/day → mean CFS "
             "(× 1000 / 1.9835). Source page: "
             '<a href="https://www.nwrfc.noaa.gov/runoff/runoff_summary.php" '
             'target="_blank" rel="noopener">NWRFC runoff summary</a>.'),
        ]
    elif chart_kind == "hydrograph":
        items = [
            HF_API_SOURCE,
            RFC_API_SOURCE,
            ("Historical daily mean (LTA)",
             "Upstream Tech API · <code>/timeseries/observations</code> · source "
             "<code>historical-percentile-daily-gauge-observation</code> · column "
             "<code>flowDailyMean</code> (the per-day climatology HF uses internally)."),
            ("Observed daily flow",
             "Local <code>runoff.db</code> · <code>daily_kaf</code> column on RUNOFF "
             "rows for site TDAO3W (natural/unregulated), converted KAF/day → mean CFS "
             "(× 1000 / 1.9835). Source page: "
             '<a href="https://www.nwrfc.noaa.gov/runoff/runoff_summary.php" '
             'target="_blank" rel="noopener">NWRFC runoff summary</a>.'),
        ]
    else:
        return ""
    lis = "\n".join(f"      <li><strong>{name}:</strong> {desc}</li>"
                     for name, desc in items)
    return (
        '<div class="sources">'
        '<div class="sources-label">Data sources</div>'
        f'<ul>\n{lis}\n    </ul>'
        '</div>'
    )


def _build_evolution_section(season_slug: str) -> str | None:
    """Build the inner tabbed (14-day / 28-day / Apr1→Today) forecast-evolution section
    for one season. Returns HTML or None if no PNGs exist for that season."""
    season = parse_season(season_slug)
    season_label = season["label"]
    tab_buttons, tab_panels = [], []
    active_assigned = False
    for sec in evolution_tabs(season_label, season["slug"]):
        img_path = latest_png(sec["subfolder"], suffix_filter=sec["suffix_filter"])
        if img_path is None:
            print(f"  WARNING: no PNG for {sec['tab_label']} ({season_label}) — skipping")
            continue
        print(f"  {sec['tab_label']} ({season_label}): {img_path.name}")
        active = " active" if not active_assigned else ""
        active_assigned = True
        # Suffix tab IDs with season slug to keep them globally unique on the page
        unique_id = f"{sec['tab_id']}-{season['slug']}"
        tab_buttons.append(
            f'<button class="tab-btn{active}" data-tab="{unique_id}">{sec["tab_label"]}</button>'
        )
        src = embed_png(img_path)
        tab_panels.append(f"""
    <div class="tab-panel{active}" id="{unique_id}">
      <p class="description">{sec['description']}</p>
      <div class="chart-wrap"><img src="{src}" alt="{sec['tab_label']}"></div>
      {sources_html(sec['chart_kind'])}
    </div>""")
    if not tab_buttons:
        return None
    return f"""
  <section class="tabbed">
    <h2>{season_label} Forecast Evolution — The Dalles</h2>
    <div class="tab-buttons">{"".join(tab_buttons)}</div>
    {"".join(tab_panels)}
  </section>"""


def build_report(default_season: str = "apr-aug") -> Path:
    # Each section's inner HTML keyed by its page-section id. Assembled at the
    # bottom of this function with the top-level nav + page-section wrappers.
    sections: dict[str, str] = {}

    # Build both season variants and wrap each in a season-content panel.
    # A top-level toggle (rendered separately below) flips between them.
    season_panels = []
    season_buttons = []
    for slug in ("apr-aug", "apr-sep"):
        season = parse_season(slug)
        active = " active" if slug == default_season else ""
        evo_html = _build_evolution_section(slug)
        if evo_html is None:
            continue
        season_buttons.append(
            f'<button class="season-toggle-btn{active}" '
            f'data-season="season-{season["slug"]}">{season["label"]}</button>'
        )
        season_panels.append(f"""
  <div class="season-content{active}" id="season-{season['slug']}">
    {evo_html}
  </div>""")

    if season_panels:
        sections["page-evolution"] = f"""
  <div class="season-toggle">
    <span class="season-toggle-label">Season window:</span>
    <div class="season-toggle-buttons">{"".join(season_buttons)}</div>
  </div>
{"".join(season_panels)}"""

    # Monthly bar charts grouped into a single tabbed section
    bar_secs = bar_chart_sections()
    for sec in bar_secs:
        print(f"  bar_chart: {sec['img_path'].name}")
    if bar_secs:
        tab_buttons = []
        tab_panels = []
        for i, sec in enumerate(bar_secs):
            tab_key   = sec["tab_key"]
            tab_label = sec["tab_label"]
            active = " active" if i == 0 else ""
            tab_buttons.append(
                f'<button class="tab-btn{active}" data-tab="tab-{tab_key}">{tab_label}</button>'
            )
            src = embed_png(sec["img_path"])
            tab_panels.append(f"""
    <div class="tab-panel{active}" id="tab-{tab_key}">
      <p class="description">{sec['description']}</p>
      <div class="chart-wrap"><img src="{src}" alt="{sec['title']}"></div>
      {sources_html("bar_chart")}
    </div>""")

        sections["page-volumes"] = f"""
  <section class="tabbed">
    <h2>Cumulative Volume Comparison — The Dalles (Monthly Inits)</h2>
    <div class="tab-buttons">{"".join(tab_buttons)}</div>
    {"".join(tab_panels)}
  </section>"""

    # Daily-flow forecast skill: forecast-vs-observed scatter grid by lead time.
    qq_path = latest_png("qq_lead_time")
    if qq_path is not None:
        print(f"  qq_lead_time: {qq_path.name}")
        src = embed_png(qq_path)
        sections["page-skill"] = f"""
  <section>
    <h2>Daily Flow Forecast Skill — by Lead Time</h2>
    <p class="description">
      Scatter of daily-mean forecast (Y) vs observed daily-mean discharge (X) for operational
      forecasts since March 1 of the current year, stratified by lead time. One row per lead
      (every 10 days, auto-extended as more verifying observations accrue), two columns
      (HydroForecast / NWRFC ESP). Points are colored <strong style="color:#1f77b4">blue</strong>
      where the forecast is above observed (over-forecast),
      <strong style="color:#d62728">red</strong> where below (under-forecast), and gray when
      within ±5% of observed. A summary table at the top compares MAE, RMSE, and R² for the
      two models side-by-side at every lead.
    </p>
    <div class="chart-wrap"><img src="{src}" alt="QQ Lead Time"></div>
    {sources_html("qq_lead_time")}
  </section>"""

    # Interactive Plotly hydrograph (daily flow time series). Reads the latest
    # *_snippet.html produced by scripts/hydrograph.py (no plotly.js inline —
    # the report HTML loads plotly.js from CDN in <head>).
    hydro_dir = RESULTS_DIR / "hydrograph"
    hydro_snippets = sorted(hydro_dir.glob("*_snippet.html")) if hydro_dir.exists() else []
    if hydro_snippets:
        snippet_path = hydro_snippets[-1]
        print(f"  hydrograph: {snippet_path.name}")
        snippet_html = snippet_path.read_text()
        sections["page-hydrograph"] = f"""
  <section>
    <h2>Daily Hydrograph — The Dalles</h2>
    <p class="description">
      Interactive daily-flow chart (CFS). Each forecast is a legend-toggleable group of three traces:
      mean line, 50% confidence interval (Q25–Q75), and 90% confidence interval (Q05–Q95). NWRFC ESP
      mean is shown as a dashed line in matching color. The historical daily mean (LTA) is the dotted
      black line; observed daily flow from the local DB is the solid dark line. By default only
      today's HF + RFC forecast is visible alongside LTA and observed — click any other init in
      the legend to enable it. Init cadence: every 1st &amp; 15th of each month back to the earliest
      HF availability, plus rolling T-10 and T-20 day forecasts (deduped where within 1 day of a
      calendar anchor).
    </p>
    {snippet_html}
    {sources_html("hydrograph")}
  </section>"""

    # ── Assemble top-level sticky nav + page-section wrappers ────────────────
    # Order (left→right): Forecast Evolution first (default landing), then
    # Daily Hydrograph, Cumulative Volumes, Forecast Skill.
    section_order = [
        ("page-evolution",  "Forecast Evolution"),
        ("page-hydrograph", "Daily Hydrograph"),
        ("page-volumes",    "Cumulative Volumes"),
        ("page-skill",      "Forecast Skill"),
    ]
    nav_buttons: list[str] = []
    page_sections: list[str] = []
    first_active = True
    for section_id, label in section_order:
        if section_id not in sections:
            continue
        active = " active" if first_active else ""
        nav_buttons.append(
            f'<button class="section-nav-btn{active}" '
            f'data-section="{section_id}">{label}</button>'
        )
        page_sections.append(
            f'<div class="page-section{active}" id="{section_id}">\n'
            f'{sections[section_id]}\n'
            f'</div>'
        )
        first_active = False

    body_content = (
        f'<nav class="section-nav">{"".join(nav_buttons)}</nav>\n'
        + "".join(page_sections)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HydroForecast Weekly Report — {TODAY.strftime('%B %-d, %Y')}</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f5;
      color: #222;
      padding: 40px 32px;
      max-width: 1100px;
      margin: 0 auto;
    }}
    header {{
      border-bottom: 2px solid #ddd;
      padding-bottom: 20px;
      margin-bottom: 36px;
    }}
    header h1 {{
      font-size: 1.4rem;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    header p {{
      font-size: 0.85rem;
      color: #666;
    }}
    section {{
      background: white;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      padding: 28px 32px;
      margin-bottom: 32px;
    }}
    section h2 {{
      font-size: 1.05rem;
      font-weight: 700;
      margin-bottom: 8px;
      color: #1a1a1a;
    }}
    .description {{
      font-size: 0.85rem;
      color: #555;
      line-height: 1.55;
      margin-bottom: 20px;
      max-width: 780px;
    }}
    .chart-wrap img {{
      width: 100%;
      height: auto;
      display: block;
      border-radius: 4px;
    }}
    .file-note {{
      font-size: 0.75rem;
      color: #aaa;
      margin-top: 10px;
      text-align: right;
    }}
    .sources {{
      margin-top: 18px;
      padding: 12px 16px;
      background: #f7f9fb;
      border-left: 3px solid #b3c7d6;
      border-radius: 4px;
    }}
    .sources-label {{
      font-size: 0.78rem;
      font-weight: 600;
      color: #1a4f7a;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .sources ul {{
      margin: 0;
      padding-left: 20px;
      font-size: 0.78rem;
      color: #444;
      line-height: 1.55;
    }}
    .sources li {{ margin-bottom: 3px; }}
    .sources code {{
      background: #e8eef3;
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 0.92em;
    }}
    .sources a {{ color: #1a4f7a; text-decoration: none; }}
    .sources a:hover {{ text-decoration: underline; }}
    footer {{
      font-size: 0.78rem;
      color: #aaa;
      text-align: center;
      margin-top: 8px;
    }}
    .tab-buttons {{
      display: flex;
      gap: 4px;
      border-bottom: 1px solid #ddd;
      margin: 14px 0 20px;
    }}
    .tab-btn {{
      background: none;
      border: none;
      padding: 8px 18px;
      font-size: 0.88rem;
      font-weight: 500;
      color: #777;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      transition: all 0.15s;
    }}
    .tab-btn:hover {{ color: #1a1a1a; }}
    .tab-btn.active {{
      color: #1a4f7a;
      border-bottom-color: #1a4f7a;
      font-weight: 600;
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}

    /* Top-level season toggle (Apr-Aug vs Apr-Sep) */
    .season-toggle {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 20px;
      padding: 14px 18px;
      background: white;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }}
    .season-toggle-label {{
      font-size: 0.85rem;
      color: #555;
      font-weight: 600;
    }}
    .season-toggle-buttons {{ display: flex; gap: 4px; }}
    .season-toggle-btn {{
      background: #f3f3f3;
      border: 1px solid #ddd;
      padding: 6px 16px;
      font-size: 0.85rem;
      font-weight: 500;
      color: #555;
      cursor: pointer;
      border-radius: 4px;
      transition: all 0.15s;
    }}
    .season-toggle-btn:hover {{ background: #e8e8e8; }}
    .season-toggle-btn.active {{
      background: #1a4f7a;
      border-color: #1a4f7a;
      color: white;
    }}
    .season-content {{ display: none; }}
    .season-content.active {{ display: block; }}

    /* ── Top-level section nav (sticky bar, single-section visibility) ──── */
    .section-nav {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: #f5f5f5;
      border-bottom: 1px solid #ddd;
      padding: 12px 0;
      margin: -16px 0 28px;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .section-nav-btn {{
      background: white;
      border: 1px solid #ccc;
      padding: 9px 22px;
      font-size: 0.92rem;
      font-weight: 500;
      color: #444;
      cursor: pointer;
      border-radius: 4px;
      transition: all 0.15s;
    }}
    .section-nav-btn:hover {{ background: #ebebeb; }}
    .section-nav-btn.active {{
      background: #1a4f7a;
      border-color: #1a4f7a;
      color: white;
    }}
    .page-section {{ display: none; }}
    .page-section.active {{ display: block; }}

    /* When printing, always show every section; hide the nav */
    @media print {{
      .section-nav {{ display: none !important; }}
      .page-section {{ display: block !important; }}
    }}
  </style>
</head>
<body>

<header>
  <h1>HydroForecast Weekly Report — {TODAY.strftime('%B %-d, %Y')}</h1>
  <p>Columbia River Basin &nbsp;·&nbsp; The Dalles &nbsp;·&nbsp; Generated {TODAY.isoformat()}</p>
</header>

{body_content}

<footer>Upstream Tech · HydroForecast · Internal use only</footer>

<script>
  // Inner tab toggle (14-day / 30-day, April / May, etc.) — scoped to each .tabbed section
  document.querySelectorAll('.tabbed').forEach(function(section) {{
    var btns = section.querySelectorAll('.tab-btn');
    var panels = section.querySelectorAll('.tab-panel');
    btns.forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        btns.forEach(function(b) {{ b.classList.remove('active'); }});
        panels.forEach(function(p) {{ p.classList.remove('active'); }});
        btn.classList.add('active');
        var target = section.querySelector('#' + btn.dataset.tab);
        if (target) target.classList.add('active');
      }});
    }});
  }});

  // Top-level season toggle (Apr-Aug / Apr-Sep) — page-wide
  document.querySelectorAll('.season-toggle-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.season-toggle-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      document.querySelectorAll('.season-content').forEach(function(c) {{ c.classList.remove('active'); }});
      btn.classList.add('active');
      var target = document.getElementById(btn.dataset.season);
      if (target) target.classList.add('active');
    }});
  }});

  // Page-level section nav — shows one .page-section at a time. After making a
  // section visible, ask Plotly to resize any charts inside it (Plotly mis-sizes
  // charts whose container was display:none at first render).
  document.querySelectorAll('.section-nav-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.section-nav-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      document.querySelectorAll('.page-section').forEach(function(s) {{ s.classList.remove('active'); }});
      btn.classList.add('active');
      var target = document.getElementById(btn.dataset.section);
      if (!target) return;
      target.classList.add('active');
      if (window.Plotly) {{
        target.querySelectorAll('.plotly-graph-div').forEach(function(el) {{
          Plotly.Plots.resize(el);
        }});
      }}
    }});
  }});
</script>

</body>
</html>"""

    out_dir = RESULTS_DIR / "weekly_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weekly_report_{TODAY}.html"
    out_path.write_text(html)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--default-season", choices=["apr-aug", "apr-sep"], default="apr-aug",
                        help="Which season tab is initially shown when the report is opened "
                             "(default: apr-aug). Both seasons are always embedded.")
    args = parser.parse_args()
    print(f"Building weekly report for {TODAY} (default season: {args.default_season})...")
    out = build_report(args.default_season)
    print(f"Saved: {out}")
