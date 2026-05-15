#!/bin/bash
# run_weekly.sh
# One-shot runner for the HydroForecast weekly report.
#
# Generates one HTML report containing BOTH season views (Apr-Aug and Apr-Sep)
# with a top-level toggle to switch between them.
#
# Usage:
#     ./run_weekly.sh                              # default Apr-Aug visible first
#     ./run_weekly.sh --default-season apr-sep     # Apr-Sep visible first
#
# Reads UPSTREAM_API_KEY from "NWRFC obs scraper/.env".
# Run ./setup.sh once before using this script on a new machine.

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

# Load API key from scraper's .env
if [ -f "$SCRAPER_DIR/.env" ]; then
    set -a
    source "$SCRAPER_DIR/.env"
    set +a
fi

if [ -z "$UPSTREAM_API_KEY" ]; then
    echo "ERROR: UPSTREAM_API_KEY not set (expected in $SCRAPER_DIR/.env)" >&2
    exit 1
fi

# pnw_volume_forecast_plot.py and apr_aug_forecast_evolution.py expect HF_API_KEY
export HF_API_KEY="$UPSTREAM_API_KEY"

step() { printf "\n\033[1;34m== %s ==\033[0m\n" "$1"; }

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

