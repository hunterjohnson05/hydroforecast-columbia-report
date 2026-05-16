#!/bin/bash
# run_weekly.sh
# One-shot runner for the HydroForecast weekly Columbia River report.
#
# Generates one HTML report containing BOTH season views (Apr-Aug and Apr-Sep)
# with a top-level toggle to switch between them.
#
# On first run: prompts for your HydroForecast API key, installs Python
# packages, and backfills the local database (~10 min). Subsequent runs
# go straight to generating the report.
#
# Usage:
#     ./run_weekly.sh                              # default Apr-Aug visible first
#     ./run_weekly.sh --default-season apr-sep     # Apr-Sep visible first

set -e   # exit on first error

# Parse --default-season flag (default apr-aug)
DEFAULT_SEASON="apr-aug"
while [ $# -gt 0 ]; do
    case "$1" in
        --default-season)
            DEFAULT_SEASON="$2"; shift 2
            ;;
        --default-season=*)
            DEFAULT_SEASON="${1#*=}"; shift
            ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'; exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2; exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRAPER_DIR="$SCRIPT_DIR/NWRFC obs scraper"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"
PYTHON=python3

step() { printf "\n\033[1;34m== %s ==\033[0m\n" "$1"; }

# ── First-run setup (skipped on subsequent runs) ──────────────────────────────

ENV_FILE="$SCRAPER_DIR/.env"
DB_FILE="$SCRAPER_DIR/runoff.db"

if [ ! -f "$ENV_FILE" ]; then
    step "First-time setup: API key"
    echo ""
    echo "Enter your HydroForecast API key."
    echo "Find it in the HydroForecast dashboard under:"
    echo "  Shared Regional → Pacific Northwest project → API Keys"
    echo ""
    printf "API key: "
    read -r api_key
    if [ -z "$api_key" ]; then
        echo "ERROR: API key cannot be empty." >&2
        exit 1
    fi
    echo "UPSTREAM_API_KEY=$api_key" > "$ENV_FILE"
    echo "✓ API key saved."
fi

if [ ! -f "$DB_FILE" ]; then
    step "First-time setup: Python packages"
    "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    echo "✓ Packages installed."

    step "First-time setup: LTA normals (one-time NWRFC scrape)"
    cd "$SCRAPER_DIR" && "$PYTHON" scrape_lta_normals.py TDAO3W
    echo "✓ LTA normals cached."

    step "First-time setup: database backfill (~10 min)"
    echo "Fetching NWRFC runoff observations from Oct 1 of the current water year to today..."
    cd "$SCRAPER_DIR" && "$PYTHON" backfill.py
    echo "✓ Database ready."
fi

# ── Load API key ──────────────────────────────────────────────────────────────

set -a; source "$ENV_FILE"; set +a

if [ -z "$UPSTREAM_API_KEY" ]; then
    echo "ERROR: UPSTREAM_API_KEY not set in $ENV_FILE" >&2
    exit 1
fi

# pnw_volume_forecast_plot.py and apr_aug_forecast_evolution.py expect HF_API_KEY
export HF_API_KEY="$UPSTREAM_API_KEY"

# ── Weekly report ─────────────────────────────────────────────────────────────

step "0/10 · backfill.py (catch up DB to today)"
BACKFILL_START=$(date -v-14d +%Y-%m-%d)
cd "$SCRAPER_DIR" && "$PYTHON" backfill.py --start "$BACKFILL_START"

step "1/10 · forecast_comparison.py (HTML tables)"
cd "$SCRAPER_DIR" && "$PYTHON" forecast_comparison.py

step "2/10 · forecast_bar_chart.py (monthly init bar charts)"
cd "$SCRAPER_DIR" && "$PYTHON" forecast_bar_chart.py

step "3/10 · pnw_volume_forecast_plot.py (Apr-Aug boxplot)"
cd "$SCRIPTS_DIR" && "$PYTHON" pnw_volume_forecast_plot.py --season apr-aug

step "4/10 · pnw_volume_forecast_plot.py (Apr-Sep boxplot)"
cd "$SCRIPTS_DIR" && "$PYTHON" pnw_volume_forecast_plot.py --season apr-sep

step "5/10 · apr_aug_forecast_evolution.py (Apr-Aug LTA % chart)"
cd "$SCRIPTS_DIR" && "$PYTHON" apr_aug_forecast_evolution.py --season apr-aug

step "6/10 · apr_aug_forecast_evolution.py (Apr-Sep LTA % chart)"
cd "$SCRIPTS_DIR" && "$PYTHON" apr_aug_forecast_evolution.py --season apr-sep

step "7/10 · compute_daily_flow.py (refresh daily_kaf for any recent NULLs)"
cd "$SCRAPER_DIR" && "$PYTHON" compute_daily_flow.py

step "8/10 · qq_lead_time.py (daily flow Q-Q scatter grid)"
cd "$SCRIPTS_DIR" && "$PYTHON" qq_lead_time.py

step "9/10 · hydrograph.py (interactive Plotly daily-flow chart)"
cd "$SCRIPTS_DIR" && "$PYTHON" hydrograph.py

step "10/10 · build_report.py (assemble weekly_report HTML, both seasons inside)"
cd "$SCRIPTS_DIR" && "$PYTHON" build_report.py --default-season "$DEFAULT_SEASON"

REPORT="$SCRIPTS_DIR/results/weekly_reports/weekly_report_$(date +%Y-%m-%d).html"
printf "\n\033[1;32mDone.\033[0m  Report: %s\n" "$REPORT"
open "$REPORT"
