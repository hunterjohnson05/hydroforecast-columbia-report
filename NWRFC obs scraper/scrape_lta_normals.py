#!/usr/bin/env python3
"""
scrape_lta_normals.py
---------------------
Fetch the 30-year normal LTAs (APR-AUG and APR-SEP) from NWRFC's natural
forecasts page and cache them in the local lta_normals table.

Source URL pattern:
    https://www.nwrfc.noaa.gov/natural/plot/nat_forecasts.php?id=<HB5_ID>

The natural-forecasts page accepts both the W-suffix (TDAO3W) and bare
(TDAO3) forms; this scraper passes whatever HB5 ID it receives. Some sites
(typically heavily regulated dams like Bonneville) render the page but have
no published seasonal normals — those are skipped with a warning.

Usage:
    /opt/anaconda3/bin/python3 scrape_lta_normals.py                    # default sites
    /opt/anaconda3/bin/python3 scrape_lta_normals.py TDAO3W BIDC2 LCBC2 # explicit list
    /opt/anaconda3/bin/python3 scrape_lta_normals.py --refresh          # re-fetch everything
"""

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from database import create_tables, get_connection, upsert_lta_normal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

NAT_FORECASTS_URL = "https://www.nwrfc.noaa.gov/natural/plot/nat_forecasts.php"
USER_AGENT        = "runoff-tracker/1.0 (research)"
REQUEST_TIMEOUT   = 30
DELAY_SECONDS     = 3   # polite pause between sites

# Default site list — start small, extend as we add coverage.
DEFAULT_SITES = ["TDAO3W"]


# Regex patterns — built once.
PERIOD_RE = re.compile(
    r'id="normals_period">\(([^)]+)\)</span>',
    re.IGNORECASE,
)
# Page layout: each forecast-period row (APR-AUG, APR-SEP) holds many ESP
# forecast columns, and the FINAL cell of the row (immediately before </tr>)
# is the 30-year-normal value. We anchor on </tr> so the lazy `.*?` skips
# past the other values and only captures the last <font>NUMBER</font>.
def _value_re(label: str) -> re.Pattern:
    return re.compile(
        r"<font[^>]*>\s*" + re.escape(label) + r"\s*</font>\s*</td>"
        r".*?"
        r"<font[^>]*>\s*([\d,]+)\s*</font>\s*</td>\s*</tr>",
        re.DOTALL | re.IGNORECASE,
    )

APR_AUG_RE = _value_re("APR-AUG")
APR_SEP_RE = _value_re("APR-SEP")


def fetch_html(site_id: str) -> tuple[str, str]:
    """Fetch the natural-forecasts page; return (html, full_url)."""
    url = f"{NAT_FORECASTS_URL}?id={site_id}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text, url


def parse_normals(html: str) -> dict:
    """
    Extract Apr-Aug LTA, Apr-Sep LTA, and reference period from the HTML.
    Returns dict with keys 'apr-aug', 'apr-sep' (KAF floats, may be None) and 'period'.
    Takes the FIRST occurrence of each label, which corresponds to the 30-year
    normals table on the page.
    """
    out = {"apr-aug": None, "apr-sep": None, "period": None}

    pm = PERIOD_RE.search(html)
    if pm:
        out["period"] = pm.group(1).strip()

    am = APR_AUG_RE.search(html)
    if am:
        out["apr-aug"] = float(am.group(1).replace(",", ""))

    sm = APR_SEP_RE.search(html)
    if sm:
        out["apr-sep"] = float(sm.group(1).replace(",", ""))

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sites", nargs="*", default=DEFAULT_SITES,
                        help="HB5 site IDs to scrape (default: TDAO3W)")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch all sites even if cached values exist (always upserts)")
    args = parser.parse_args()

    conn = get_connection()
    create_tables(conn)

    summary = {"updated": 0, "skipped": 0, "errors": 0, "no_data": 0}

    for site_id in args.sites:
        # Skip sites already cached unless --refresh is set
        if not args.refresh:
            row = conn.execute(
                "SELECT 1 FROM lta_normals WHERE site_id = ? LIMIT 1", (site_id,)
            ).fetchone()
            if row:
                logger.info(f"{site_id}: already cached — skip (use --refresh to re-fetch)")
                summary["skipped"] += 1
                continue

        try:
            html, url = fetch_html(site_id)
        except Exception as exc:
            logger.warning(f"{site_id}: fetch failed — {exc}")
            summary["errors"] += 1
            time.sleep(DELAY_SECONDS)
            continue

        normals = parse_normals(html)
        if normals["apr-aug"] is None and normals["apr-sep"] is None:
            logger.warning(
                f"{site_id}: page returned but no APR-AUG / APR-SEP normals found "
                f"(likely a regulated site without published seasonal forecasts)"
            )
            summary["no_data"] += 1
            time.sleep(DELAY_SECONDS)
            continue

        fetched_at = datetime.now(timezone.utc).isoformat()
        period = normals.get("period")
        for season_key in ("apr-aug", "apr-sep"):
            v = normals[season_key]
            if v is None:
                continue
            upsert_lta_normal(conn, site_id, season_key, v, period, url, fetched_at)
            logger.info(
                f"{site_id} {season_key.upper()}: {v:,.0f} KAF "
                f"({v/1000:.2f} MAF, period {period or 'unknown'})"
            )
        conn.commit()
        summary["updated"] += 1
        time.sleep(DELAY_SECONDS)

    conn.close()
    logger.info(
        f"Done. updated={summary['updated']} skipped={summary['skipped']} "
        f"no_data={summary['no_data']} errors={summary['errors']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
