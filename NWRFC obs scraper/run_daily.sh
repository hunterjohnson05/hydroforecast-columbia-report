#!/bin/bash
# run_daily.sh
# Wrapper called by launchd. Runs the Python scraper then sleeps briefly
# so the process lifetime exceeds launchd's 10-second minimum runtime
# requirement (ThrottleInterval default). Without this, launchd marks
# every run as a crash (exit 78) and backs off on future StartInterval fires.

# Debug: log launch context immediately
echo "[run_daily.sh] Started at $(date) as $(whoami) in $(pwd)" >&2
echo "[run_daily.sh] PATH=$PATH" >&2

SCRIPT_DIR="/Users/hunterjohnson/Desktop/Claude Code/NWRFC obs scraper"

if ! cd "$SCRIPT_DIR"; then
    echo "[run_daily.sh] ERROR: could not cd to $SCRIPT_DIR" >&2
    sleep 11
    exit 1
fi

echo "[run_daily.sh] cd OK, running python..." >&2

PYTHON=/opt/anaconda3/bin/python3
if [ ! -x "$PYTHON" ]; then
    echo "[run_daily.sh] ERROR: python not found at $PYTHON" >&2
    sleep 11
    exit 1
fi

"$PYTHON" run_daily.py
EXIT_CODE=$?
echo "[run_daily.sh] python exited with code $EXIT_CODE" >&2

sleep 11
exit $EXIT_CODE
