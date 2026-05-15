#!/usr/bin/env bash
# setup_launchd.sh
# ----------------
# Installs (or reinstalls) the daily scraper as a macOS launchd user agent.
# Run once from inside the project folder:
#   bash setup_launchd.sh

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_SRC="$PROJECT_DIR/com.runofftracker.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.runofftracker.daily.plist"
LOG_DIR="$PROJECT_DIR/logs"
LABEL="com.runofftracker.daily"

echo "=== NWRFC obs scraper — launchd setup ==="
echo "Project dir: $PROJECT_DIR"

# 1. Create logs and daily_results directories
mkdir -p "$LOG_DIR"
mkdir -p "$PROJECT_DIR/daily_results"
echo "✓ Log directory:          $LOG_DIR"
echo "✓ Daily results directory: $PROJECT_DIR/daily_results"

# 2. Install Python dependencies
echo "Installing Python dependencies..."
pip3 install --quiet requests beautifulsoup4 lxml
echo "✓ Dependencies installed"

# 3. Unload existing job if present
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "Unloading existing launchd job..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# 4. Copy plist to LaunchAgents
cp "$PLIST_SRC" "$PLIST_DST"
echo "✓ Plist installed to $PLIST_DST"

# 5. Load the job
launchctl load "$PLIST_DST"
echo "✓ Job loaded: $LABEL"

echo ""
echo "=== Done — scraper runs daily at 08:00 ==="
echo ""
echo "Useful commands:"
echo "  Run manually now:  python3 \"$PROJECT_DIR/run_daily.py\""
echo "  View log:          tail -f \"$PROJECT_DIR/logs/scraper.log\""
echo "  Check job status:  launchctl list | grep runofftracker"
echo "  Disable job:       launchctl unload \"$PLIST_DST\""
echo "  Re-enable job:     launchctl load   \"$PLIST_DST\""
