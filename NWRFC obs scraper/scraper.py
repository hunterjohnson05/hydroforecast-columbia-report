"""
scraper.py
----------
Fetch and parse the NWRFC Water Supply Runoff Summary page.

Page structure (as of water year 2026):
  - All station data lives in a single large <table> (index 1 of 3 on page)
  - Every station occupies exactly 5 consecutive rows:
      Row 0: 1-cell station name header, e.g.
             "Columbia River - Mica Dam (MCDQ2W)[122280000 Adjusted Runoff]"
      Row 1: 20-cell column header row (first cell is empty)
      Row 2: RUNOFF data row
      Row 3: AVERAGE data row
      Row 4: PCT AVG data row
  - Cumulative columns are named "Oct1-<MMdd>", "Jan1-<MMdd>", "Apr1-<MMdd>"
    where <MMdd> changes each day — matched by prefix.

Data cleaning applied before DB insert:
  - site_name: stripped of trailing "(HB5ID)[USGS# Type]" → human-readable only
  - obs_date:  normalized from "YYYY/MM/DD" → ISO "YYYY-MM-DD"
  - row_type:  "PCT AVG" → "PCT_AVG"
"""

import re
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional

from config import NWRFC_URL, REQUEST_TIMEOUT, USER_AGENT, SITES

logger = logging.getLogger(__name__)

# Canonical month order on the page
MONTH_FIELDS = ["oct", "nov", "dec", "jan", "feb", "mar",
                "apr", "may", "jun", "jul", "aug", "sep"]
MONTH_HEADERS = ["OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
                 "APR", "MAY", "JUN", "JUL", "AUG", "SEP"]


def fetch_page(url: str = NWRFC_URL) -> str:
    """Download the runoff summary page and return raw HTML."""
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    logger.info(f"Fetched {url} — HTTP {response.status_code} ({len(response.content):,} bytes)")
    return response.text


def _to_float(text: str) -> Optional[float]:
    """Convert a cell string to float; return None if blank or non-numeric."""
    cleaned = text.strip().replace(",", "")
    if not cleaned or cleaned in ("-", "--", "N/A", "n/a"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(text: str) -> Optional[int]:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


def _clean_site_name(raw: str) -> str:
    """
    Strip the trailing HB5 ID and USGS station number from a station header.

    Input:  "Columbia River - Mica Dam (MCDQ2W)[122280000 Adjusted Runoff]"
    Output: "Columbia River - Mica Dam"
    """
    if not raw:
        return raw
    # Remove everything from the first " (" onwards that matches the pattern
    cleaned = re.sub(r"\s*\([A-Z0-9]+\)\[.*?\]\s*$", "", raw).strip()
    return cleaned if cleaned else raw


def _normalize_date(raw: str) -> str:
    """
    Normalize the page's date format to ISO 8601.

    Input:  "2026/04/23"
    Output: "2026-04-23"
    """
    return raw.replace("/", "-") if raw else raw


def parse_page(html: str) -> list[dict]:
    """
    Parse the NWRFC runoff page and return a list of record dicts ready for
    upsert_observation().  Each dict represents one (site, date, row_type) row.

    Page structure (confirmed):
      - All station data lives in a single large <table> (index 1 of 3 on page)
      - Every station occupies exactly 5 consecutive rows:
          Row 0: 1-cell station name header, e.g.
                 "Columbia River - Mica Dam (MCDQ2W)[122280000 Adjusted Runoff]"
          Row 1: 20-cell column header row (first cell is empty)
          Row 2: RUNOFF data row
          Row 3: AVERAGE data row
          Row 4: PCT AVG data row
      - Cumulative columns are named "Oct1-<MMdd>", "Jan1-<MMdd>", "Apr1-<MMdd>"
        where <MMdd> changes each day — we match by prefix.
    """
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.utcnow().isoformat()
    records: list[dict] = []

    all_tables = soup.find_all("table")
    logger.debug(f"Found {len(all_tables)} tables on page")

    # The main data table is the one with many rows and "HB5 ID" in row 1
    main_table = None
    for t in all_tables:
        rows = t.find_all("tr")
        if len(rows) > 10:
            # Check if second row looks like the column header row
            cells_row1 = [c.get_text(strip=True) for c in rows[1].find_all(["td", "th"])]
            if "HB5 ID" in cells_row1:
                main_table = t
                break

    if main_table is None:
        logger.warning("Could not locate main data table — page structure may have changed")
        return records

    all_rows = main_table.find_all("tr")
    logger.debug(f"Main table has {len(all_rows)} rows")

    site_name: Optional[str] = None
    col: dict[str, int] = {}
    cumul_oct_idx = cumul_jan_idx = cumul_apr_idx = None

    for row in all_rows:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if not cells:
            continue

        n = len(cells)

        # ---- Station name header (single cell) ----
        if n == 1:
            site_name = cells[0]
            continue

        # ---- Column header row (first cell is empty, second is "HB5 ID") ----
        if n >= 2 and cells[0] == "" and cells[1] == "HB5 ID":
            col = {h: i for i, h in enumerate(cells)}
            cumul_oct_idx = cumul_jan_idx = cumul_apr_idx = None
            for i, h in enumerate(cells):
                if re.match(r"Oct1-", h, re.IGNORECASE):
                    cumul_oct_idx = i
                elif re.match(r"Jan1-", h, re.IGNORECASE):
                    cumul_jan_idx = i
                elif re.match(r"Apr1-", h, re.IGNORECASE):
                    cumul_apr_idx = i
            continue

        # ---- Data row ----
        row_type = cells[0]
        if row_type not in ("RUNOFF", "AVERAGE", "PCT AVG"):
            continue

        if not col:
            logger.warning("Data row encountered before any header row — skipping")
            continue

        row_type_db = row_type.replace(" ", "_")  # PCT AVG → PCT_AVG

        site_id  = cells[col["HB5 ID"]]   if "HB5 ID"   in col and len(cells) > col["HB5 ID"]   else None
        obs_date = cells[col["OBS DATE"]]  if "OBS DATE" in col and len(cells) > col["OBS DATE"]  else None
        wy_raw   = cells[col["WY"]]        if "WY"       in col and len(cells) > col["WY"]        else ""

        # Apply site filter from config
        if SITES and site_id not in SITES:
            continue

        record: dict = {
            "site_id":    site_id,
            "site_name":  _clean_site_name(site_name),   # human-readable only
            "obs_date":   _normalize_date(obs_date),     # ISO YYYY-MM-DD
            "water_year": _to_int(wy_raw),
            "scraped_at": scraped_at,
            "row_type":   row_type_db,
            "oct": None, "nov": None, "dec": None,
            "jan": None, "feb": None, "mar": None,
            "apr": None, "may": None, "jun": None,
            "jul": None, "aug": None, "sep": None,
            "cumul_oct_to_date": None,
            "cumul_jan_to_date": None,
            "cumul_apr_to_date": None,
        }

        # Monthly values
        for month_hdr, field in zip(MONTH_HEADERS, MONTH_FIELDS):
            idx = col.get(month_hdr)
            if idx is not None and len(cells) > idx:
                record[field] = _to_float(cells[idx])

        # Cumulative period values
        if cumul_oct_idx is not None and len(cells) > cumul_oct_idx:
            record["cumul_oct_to_date"] = _to_float(cells[cumul_oct_idx])
        if cumul_jan_idx is not None and len(cells) > cumul_jan_idx:
            record["cumul_jan_to_date"] = _to_float(cells[cumul_jan_idx])
        if cumul_apr_idx is not None and len(cells) > cumul_apr_idx:
            record["cumul_apr_to_date"] = _to_float(cells[cumul_apr_idx])

        records.append(record)

    logger.info(f"Parsed {len(records)} records from page")
    return records
