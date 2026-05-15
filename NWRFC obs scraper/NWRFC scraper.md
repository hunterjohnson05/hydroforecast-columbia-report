---
name: NWRFC Scraper
description: Project context for the NWRFC Columbia Basin runoff scraper — file map, DB schema, launchd setup, known gotchas, duplicate sites, watershed groups, and deferred TODOs.
---

# NWRFC Runoff Scraper — Project Context

## What This Project Does
Scrapes the NWRFC Water Supply Runoff Summary page daily, stores cumulative monthly
streamflow volumes in a local SQLite database, exports a daily CSV snapshot, and
generates an interactive HTML visualization.

**Source URL:** https://www.nwrfc.noaa.gov/runoff/runoff_summary.php  
**Project path:** `/Users/hunterjohnson/Desktop/Claude Code/NWRFC obs scraper/`  
**Python:** `/opt/anaconda3/bin/python3`

---

## File Map

| File | Purpose |
|---|---|
| `config.py` | Central config — all paths derived from `PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))` |
| `scraper.py` | Fetches and parses the NWRFC page |
| `database.py` | SQLite helpers — `create_tables`, `upsert_observation`, `log_scrape` |
| `run_daily.py` | Main entry point — orchestrates fetch → parse → DB → export → viz |
| `daily_export.py` | Writes `daily_results/runoff_YYYY-MM-DD.csv` from the DB |
| `generate_viz.py` | Builds `daily_results/timeseries.html` — the interactive Plotly visualization |
| `backfill.py` | Historical backfill helper (archive URL date param format TBD — check via DevTools) |
| `com.runofftracker.daily.plist` | launchd LaunchAgent plist (source of truth — copy to ~/Library/LaunchAgents/) |
| `setup_launchd.sh` | Installs/reinstalls the launchd job |
| `run_daily.sh` | Legacy bash wrapper (no longer used by launchd — kept for manual use) |

---

## Database Schema (`runoff.db`)

### `runoff_observations`
```
site_id     TEXT    — HB5 ID (e.g. TDAO3, TDAO3W)
site_name   TEXT    — cleaned display name (regex strips "(HB5ID)[type]" suffix)
obs_date    TEXT    — ISO date YYYY-MM-DD (the date shown on the NWRFC page)
row_type    TEXT    — RUNOFF | AVERAGE | PCT_AVG
col_*       REAL    — ~18 numeric columns (daily + cumulative periods)
UNIQUE (site_id, obs_date, row_type)  — upsert safe
```

### `scrape_log`
```
id, run_at (UTC ISO), obs_date, records_in, status (success|error), message
```

---

## Duplicate Sites — Important
Seven sites appear **twice** with different `site_id` values. The `W` suffix denotes a
different measurement type (unregulated/water-supply flow vs. observed/adjusted flow):

| site_name | site_id variants |
|---|---|
| Columbia River - Bonneville Dam | BONO3W, BONO3 |
| Columbia River - Grand Coulee Dam | GCDW1W, GCDW1 |
| Columbia River - John Day Dam | JDAO3W, JDAO3 |
| Columbia River - McNary Dam | (two IDs) |
| Columbia River - The Dalles Dam | TDAO3W, TDAO3 |
| Owyhee River - below Owyhee Dam | (two IDs) |
| Henrys Fork River - at Rexburg | (two IDs) |

**Fix applied in generate_viz.py:** When a site_name is duplicated, the site_id is
appended to the legend label: e.g. "Columbia River - The Dalles Dam (TDAO3W)".

---

## Watershed Groups (used in visualization legend)
```python
WATERSHED_GROUPS = {
    "Upper Columbia (Canada)":  [...],
    "Upper Columbia (US)":      [...],
    "Snake River":              [...],
    "Clark Fork / Pend Oreille":[...],
    "Willamette / Sandy":       [...],
    "Yakima / Wenatchee":       [...],
    "Lower Snake / Clearwater": [...],
    "Lower Columbia / Willamette": [...],
    "OR High Desert":           [...],
    "Other":                    [...],
}
```
All 132 sites are assigned. Groups are used in both the time series tab and the
latest snapshot tab.

---

## launchd Automation

**Job label:** `com.runofftracker.daily`  
**Schedule:** `StartInterval = 3600` (hourly) + `RunAtLoad = true`  
**Idempotency:** `already_scraped_today()` in `run_daily.py` checks `scrape_log`
for a successful run with `run_at >= today (UTC)` — subsequent hourly fires exit immediately.

### Critical launchd Gotchas (hard-won)

1. **`StandardOutPath`/`StandardErrorPath` cannot point to `~/Desktop`**  
   macOS prevents launchd from opening I/O redirect files in the Desktop folder.
   The job silently dies in < 1s → launchd sets exit code 78 (EX_CONFIG / minimum
   runtime violation) → job stops scheduling.  
   **Fix:** Logs live at `~/Library/Logs/runofftracker/launchd.{stdout,stderr}.log`

2. **Minimum runtime (10s default)**  
   If the process exits in under 10 seconds, launchd sets exit 78 and backs off.  
   **Fix:** `run_daily.py` ends with `time.sleep(12)` after all work is done.

3. **Diagnosing the job:**
   ```bash
   launchctl print gui/$(id -u)/com.runofftracker.daily | grep -E "last exit|runs|state"
   ```
   - `last exit code = 0` → healthy
   - `last exit code = 78: EX_CONFIG` → minimum runtime or I/O redirect failure
   - `last exit code = 126` → permission denied executing the script (don't use bash wrapper pointing to Desktop)

4. **Reinstalling after plist changes:**
   ```bash
   bash setup_launchd.sh
   # or manually:
   launchctl bootout gui/$(id -u)/com.runofftracker.daily
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.runofftracker.daily.plist
   ```

5. **Log locations:**
   - `logs/scraper.log` — detailed Python scraper log (written by Python directly — OK on Desktop)
   - `~/Library/Logs/runofftracker/launchd.stdout.log` — launchd I/O capture

---

## Known Data Gaps / Quirks

- **obs_date vs. scrape date:** `obs_date` is the date shown on the NWRFC page, NOT
  the date we scraped. The page typically updates once per day but timing varies.
  If the scrape runs before the page updates, `obs_date` will be the previous day.
  Example: scraped April 25 at 10:19 AM EDT → page still showed April 24 data →
  `obs_date = 2026-04-24`. Page updated later that day but we'd already marked
  April 25 as done → April 25 obs_date was never captured.

- **`--force` flag:** Mentioned in log output but NOT yet implemented. To force a
  re-scrape, delete today's row from `scrape_log`:
  ```sql
  DELETE FROM scrape_log WHERE run_at >= date('now') AND status = 'success';
  ```

---

## Deferred / TODO
- [x] **Backfill:** ~~`backfill.py` exists but archive URL date parameter format is
      unknown — need to check via browser DevTools on the NWRFC page~~
      Resolved 2026-05-11: confirmed URL format is `?date=MM/DD/YYYY`. Verified via
      the page's "Choose Date" widget; `backfill.py` already uses this format.
- [ ] **Forecast API integration (Step 4):** User has a forecast API script to share
- [ ] **Weekly summary plots (Step 5):** Deferred until forecast integration is done
- [ ] **Site selection:** Currently tracking all 132 sites. `SITES = []` in
      `config.py` means "all". Set to a list of HB5 IDs to filter.
- [ ] **Implement `--force` flag** in `run_daily.py` to allow re-scraping same day
- [ ] **Investigate `daily_kaf` column:** Observed `None` for all rows on the
      2026-04-25 backfill (Mica Dam sample showed `daily_kaf=None` across RUNOFF,
      AVERAGE, PCT_AVG row types). Either the parser isn't picking up the daily
      value from this page layout, or the column isn't part of the archive view.
      Check whether daily_kaf populates correctly on live daily scrapes vs.
      backfills, and confirm column mapping in `scraper.py`.
- [ ] **Consider CSV download endpoint as alternative source:** The page exposes
      `/misc/downloads/index.php?type=ws_runoff&sortby=date&sortasc=true&filter=YYYYMMDD`
      which returns CSV directly. Could simplify parsing vs. HTML scraping and may
      have a more complete column set (including daily_kaf). Worth evaluating.
- [ ] **Merge external-facing PDF report with internal HTML visualization:** A
      separate external-facing PDF report exists (used in customer comms, e.g.
      Macquarie) and the internal Plotly HTML viz lives at `daily_results/timeseries.html`.
      Eventually these two outputs need to be merged or coordinated so we have one
      source of truth for what's shared internally vs. externally. Currently they
      are developed independently.
