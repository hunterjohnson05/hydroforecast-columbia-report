"""
config.py
---------
Edit this file to control which sites are tracked and where data is stored.
"""

import os

# Project root — all paths are relative to this so nothing breaks if you
# move the folder, as long as you keep the files together.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Sites to track
# ---------------------------------------------------------------------------
# List HB5 IDs (e.g. "MCDQ2W", "BCVQ2W") for the stations you care about.
# Leave as an empty list [] to capture ALL stations on the page (~132 today).
SITES = []

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(PROJECT_DIR, "runoff.db")

# ---------------------------------------------------------------------------
# Daily results export folder
# ---------------------------------------------------------------------------
DAILY_RESULTS_DIR = os.path.join(PROJECT_DIR, "daily_results")

# ---------------------------------------------------------------------------
# Source URL  (no date parameter — always fetches the latest available day)
# ---------------------------------------------------------------------------
NWRFC_URL = "https://www.nwrfc.noaa.gov/runoff/runoff_summary.php"

# ---------------------------------------------------------------------------
# HTTP settings
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 30   # seconds
USER_AGENT = "runoff-tracker/1.0 (research; contact your-email@example.com)"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
