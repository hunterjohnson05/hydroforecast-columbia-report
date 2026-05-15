#!/bin/bash
# setup.sh — run once after cloning on a new machine.
#
# 1. Prompts for your HydroForecast API key and saves it to .env
# 2. Installs Python dependencies
# 3. Fetches LTA normals from NWRFC (one-time public scrape, no API key needed)
# 4. Backfills the local runoff database from Oct 1 of the current water year
#    to today (~10 min — NWRFC requests are spaced 3 seconds apart)
#
# After this completes, run ./run_weekly.sh to generate your first report.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRAPER_DIR="$SCRIPT_DIR/NWRFC obs scraper"
ENV_FILE="$SCRAPER_DIR/.env"
PYTHON=python3

step() { printf "\n\033[1;34m== %s ==\033[0m\n" "$1"; }

echo "=== HydroForecast Weekly Report — First-Time Setup ==="

# ── 1. API key ────────────────────────────────────────────────────────────────
step "API key"
if [ -f "$ENV_FILE" ]; then
    echo "✓ .env already exists — skipping."
else
    echo ""
    echo "Enter your HydroForecast API key."
    echo "Find it at: app.hydroforecast.com → (your name, top right) → API Keys"
    echo ""
    printf "API key: "
    read -r api_key
    if [ -z "$api_key" ]; then
        echo "ERROR: API key cannot be empty." >&2
        exit 1
    fi
    echo "UPSTREAM_API_KEY=$api_key" > "$ENV_FILE"
    echo "✓ Saved to $ENV_FILE"
fi

# ── 2. Python packages ────────────────────────────────────────────────────────
step "Python packages"
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "✓ Packages installed."

# ── 3. LTA normals (one-time NWRFC scrape) ───────────────────────────────────
step "LTA normals"
cd "$SCRAPER_DIR"
"$PYTHON" scrape_lta_normals.py TDAO3W
echo "✓ LTA normals cached."

# ── 4. Full database backfill ─────────────────────────────────────────────────
step "Database backfill (this takes ~10 minutes)"
echo "Fetching NWRFC runoff observations from Oct 1 of the current water year to today..."
cd "$SCRAPER_DIR"
"$PYTHON" backfill.py
echo "✓ Database ready."

printf "\n\033[1;32m=== Setup complete. Run ./run_weekly.sh to generate your first report. ===\033[0m\n\n"
