"""
lta.py
------
Shared helper for fetching long-term average (LTA) seasonal volumes from
NWRFC's published AVERAGE row in the local runoff.db.

Both `pnw_volume_forecast_plot.py` (14-day boxplot) and
`apr_aug_forecast_evolution.py` (30-day customer-style chart) import from here
so the LTA is defined in one place.

To shift the season window — e.g. flip everything from Apr-Aug to Apr-Sep —
pass a different `months` list:

    get_lta_kaf("TDAO3W", months=APR_SEP_MONTHS)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "NWRFC obs scraper" / "runoff.db"

# Convenience constants for the most common windows
APR_AUG_MONTHS = ["apr", "may", "jun", "jul", "aug"]
APR_SEP_MONTHS = ["apr", "may", "jun", "jul", "aug", "sep"]
DEFAULT_MONTHS = APR_AUG_MONTHS

# Named season presets for CLI consumers. Keys are CLI-friendly slugs.
SEASONS = {
    "apr-aug": {
        "label":     "Apr-Aug",
        "slug":      "apr_aug",          # filesystem-safe variant
        "months":    APR_AUG_MONTHS,
        "end_month": 8,                  # last calendar-month index included
    },
    "apr-sep": {
        "label":     "Apr-Sep",
        "slug":      "apr_sep",
        "months":    APR_SEP_MONTHS,
        "end_month": 9,
    },
}


def parse_season(season: str) -> dict:
    """Return the SEASONS dict entry for a slug; raises ValueError if unknown."""
    if season not in SEASONS:
        raise ValueError(
            f"Unknown season {season!r}; choose from {list(SEASONS)}"
        )
    return SEASONS[season]


def _months_to_season(months: list[str]) -> str | None:
    """Map a months list to one of the cached season slugs, or None if it
    doesn't match any preset (caller should fall back to AVERAGE-row sum)."""
    s = list(months)
    if s == APR_AUG_MONTHS:
        return "apr-aug"
    if s == APR_SEP_MONTHS:
        return "apr-sep"
    return None


def _lta_from_average_row(conn: sqlite3.Connection,
                           db_site_id: str, months: list[str]) -> float:
    """Fallback: sum monthly columns from the most-recent AVERAGE row."""
    cols = ", ".join(months)
    row = conn.execute(f"""
        SELECT {cols} FROM runoff_observations
        WHERE site_id = ? AND row_type = 'AVERAGE'
        ORDER BY obs_date DESC LIMIT 1
    """, (db_site_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"No AVERAGE row found for site_id={db_site_id!r}")
    if any(v is None for v in row):
        raise RuntimeError(
            f"AVERAGE row for {db_site_id} has NULL values in {months}: {row}"
        )
    return float(sum(row))


def get_lta_kaf(db_site_id: str = "TDAO3W",
                months: list[str] | None = None,
                db_path: Path | None = None) -> float:
    """
    Return the LTA in KAF for the given site.

    Primary source: `lta_normals` table (1991-2020 30-year normals scraped
    from NWRFC's nat_forecasts.php — same numbers customers see). Populate
    via `NWRFC obs scraper/scrape_lta_normals.py`.

    Fallback: if no cached normal exists for the requested season (e.g. an
    arbitrary month combination, or a site that hasn't been scraped yet),
    sum the requested monthly columns from the AVERAGE row in
    runoff_observations. This uses NWRFC's full-record averages and may
    differ from the 1991-2020 normals by ~3%.

    Args:
        db_site_id: NWRFC HB5 ID (default TDAO3W = The Dalles natural/unregulated).
        months:     list of monthly column names. Defaults to Apr-Aug.
        db_path:    override the default DB path.
    """
    months = months or DEFAULT_MONTHS
    if not months:
        raise ValueError("months list cannot be empty")
    allowed = {"oct", "nov", "dec", "jan", "feb", "mar",
               "apr", "may", "jun", "jul", "aug", "sep"}
    bad = [m for m in months if m not in allowed]
    if bad:
        raise ValueError(f"Invalid month column name(s): {bad}. Must be in {allowed}.")

    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    try:
        season_slug = _months_to_season(months)
        if season_slug is not None:
            # Try the cached 30-year normal first.
            row = conn.execute(
                "SELECT lta_kaf FROM lta_normals WHERE site_id = ? AND season = ?",
                (db_site_id, season_slug),
            ).fetchone()
            if row is not None:
                return float(row[0])
            # No cached value — fall through to AVERAGE-row sum below.
        return _lta_from_average_row(conn, db_site_id, months)
    finally:
        conn.close()


def get_lta_maf(db_site_id: str = "TDAO3W",
                months: list[str] | None = None,
                db_path: Path | None = None) -> float:
    """LTA in MAF (millions of acre-feet) — see get_lta_kaf."""
    return get_lta_kaf(db_site_id, months, db_path) / 1000.0


if __name__ == "__main__":
    # Quick sanity check
    for site, label in [("TDAO3W", "The Dalles")]:
        for months, name in [(APR_AUG_MONTHS, "Apr-Aug"),
                             (APR_SEP_MONTHS, "Apr-Sep")]:
            try:
                v = get_lta_kaf(site, months)
                print(f"{label} ({site}) {name} LTA: {v:,.1f} KAF ({v/1000:.2f} MAF)")
            except Exception as e:
                print(f"{label} ({site}) {name}: ERROR — {e}")
