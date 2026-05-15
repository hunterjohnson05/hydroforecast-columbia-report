"""
runoff_tracker/database.py
--------------------------
Schema creation and upsert logic for the SQLite database.
"""

import sqlite3
from pathlib import Path
from config import DB_PATH


def get_db_path() -> str:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_daily_kaf_column(conn: sqlite3.Connection) -> bool:
    """
    Add the `daily_kaf` column to runoff_observations if it doesn't exist.
    Returns True if the column was added, False if it already existed.
    Idempotent — safe to call on every run.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runoff_observations)")}
    if "daily_kaf" in cols:
        return False
    conn.execute("ALTER TABLE runoff_observations ADD COLUMN daily_kaf REAL")
    conn.commit()
    return True


def compute_daily_flow(conn: sqlite3.Connection,
                        start_date: str | None = None,
                        end_date: str | None = None) -> int:
    """
    Compute daily_kaf for RUNOFF rows in [start_date, end_date].
    daily_kaf = today.cumul_oct_to_date - prior_day.cumul_oct_to_date,
    where prior_day = obs_date − 1 day for the same site.

    Rows that stay NULL by design:
      - Non-RUNOFF rows (AVERAGE / PCT_AVG)
      - Gap days (yesterday missing or > 1 day prior)
      - Oct 1 of any water year (NWRFC's cumul_oct_to_date column on Oct 1
        carries the prior water year's annual total — unreliable for diffing)
      - Oct 2 of any water year (Oct 1 prior is bad, so the diff is meaningless)
      - Any computed negative value (treated as NULL — physically impossible)

    If start_date / end_date are None, recomputes the full table.
    Returns the number of RUNOFF rows now populated.
    """
    ensure_daily_kaf_column(conn)

    where = "row_type = 'RUNOFF'"
    params: list = []
    if start_date:
        where += " AND obs_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND obs_date <= ?"
        params.append(end_date)

    # 1. Reset daily_kaf for affected rows so re-runs don't leave stale values.
    conn.execute(f"UPDATE runoff_observations SET daily_kaf = NULL WHERE {where}",
                 params)

    # 2. Compute daily_kaf as today − prior (prior = exactly 1 day before).
    #    Skip Oct 1 (no usable prior) and Oct 2 (prior cumul is prior-WY residue).
    conn.execute(f"""
        UPDATE runoff_observations AS r
        SET daily_kaf = r.cumul_oct_to_date - prev.cumul_oct_to_date
        FROM runoff_observations AS prev
        WHERE r.site_id = prev.site_id
          AND r.row_type = 'RUNOFF' AND prev.row_type = 'RUNOFF'
          AND prev.obs_date = date(r.obs_date, '-1 day')
          AND r.cumul_oct_to_date IS NOT NULL
          AND prev.cumul_oct_to_date IS NOT NULL
          AND substr(r.obs_date, 6, 5) NOT IN ('10-01', '10-02')
          AND {where.replace("row_type", "r.row_type")}
    """, params)

    # 3. Sanity check: NULL out negatives (physically impossible — usually means a
    #    cumul value reset or a bad source value).
    conn.execute(f"UPDATE runoff_observations SET daily_kaf = NULL "
                 f"WHERE {where} AND daily_kaf < 0", params)

    conn.commit()

    n = conn.execute(f"""
        SELECT COUNT(*) FROM runoff_observations
        WHERE {where} AND daily_kaf IS NOT NULL
    """, params).fetchone()[0]
    return n


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the schema if it doesn't already exist."""
    conn.executescript("""
        -- 30-year normal LTAs scraped from NWRFC's nat_forecasts.php page.
        -- One row per (site, season). Refreshed by scrape_lta_normals.py.
        CREATE TABLE IF NOT EXISTS lta_normals (
            site_id     TEXT NOT NULL,
            season      TEXT NOT NULL,    -- 'apr-aug' or 'apr-sep'
            lta_kaf     REAL NOT NULL,
            period      TEXT,             -- e.g., '1991-2020'
            source_url  TEXT,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (site_id, season)
        );
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runoff_observations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id             TEXT    NOT NULL,   -- HB5 ID e.g. MCDQ2W
            site_name           TEXT,               -- Human label from page header
            obs_date            TEXT    NOT NULL,   -- YYYY/MM/DD as returned by page
            water_year          INTEGER,
            scraped_at          TEXT    NOT NULL,   -- UTC ISO-8601 timestamp
            row_type            TEXT    NOT NULL,   -- RUNOFF | AVERAGE | PCT_AVG

            -- Monthly KAF values (cumulative through that month in the water year)
            oct                 REAL,
            nov                 REAL,
            dec                 REAL,
            jan                 REAL,
            feb                 REAL,
            mar                 REAL,
            apr                 REAL,
            may                 REAL,
            jun                 REAL,
            jul                 REAL,
            aug                 REAL,
            sep                 REAL,

            -- Cumulative period columns (header dates change daily, values stored here)
            cumul_oct_to_date   REAL,   -- "Oct1-MMdd" column
            cumul_jan_to_date   REAL,   -- "Jan1-MMdd" column
            cumul_apr_to_date   REAL,   -- "Apr1-MMdd" column

            UNIQUE(site_id, obs_date, row_type)
        );

        CREATE INDEX IF NOT EXISTS idx_site_date
            ON runoff_observations(site_id, obs_date);

        CREATE INDEX IF NOT EXISTS idx_obs_date
            ON runoff_observations(obs_date);

        -- Lightweight scrape log: one row per run
        CREATE TABLE IF NOT EXISTS scrape_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT    NOT NULL,
            obs_date    TEXT,               -- date found on the page
            records_in  INTEGER DEFAULT 0,
            status      TEXT    NOT NULL,   -- success | error
            message     TEXT
        );
    """)
    conn.commit()


def upsert_lta_normal(conn: sqlite3.Connection, site_id: str, season: str,
                       lta_kaf: float, period: str | None, source_url: str | None,
                       fetched_at: str) -> None:
    """Insert or replace one (site_id, season) row in lta_normals."""
    conn.execute("""
        INSERT INTO lta_normals (site_id, season, lta_kaf, period, source_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(site_id, season) DO UPDATE SET
            lta_kaf    = excluded.lta_kaf,
            period     = excluded.period,
            source_url = excluded.source_url,
            fetched_at = excluded.fetched_at
    """, (site_id, season, lta_kaf, period, source_url, fetched_at))


def get_lta_normal(conn: sqlite3.Connection, site_id: str, season: str
                    ) -> tuple[float, str | None] | None:
    """
    Look up a cached LTA. Returns (lta_kaf, period) or None if no row exists.
    """
    row = conn.execute(
        "SELECT lta_kaf, period FROM lta_normals WHERE site_id = ? AND season = ?",
        (site_id, season),
    ).fetchone()
    if row is None:
        return None
    return float(row[0]), row[1]


def insert_observation_if_missing(conn: sqlite3.Connection, record: dict) -> bool:
    """
    Insert a record only if (site_id, obs_date, row_type) doesn't already exist.
    Returns True if a new row was inserted, False if the row already existed
    (in which case existing values are preserved untouched).

    Use this for backfill operations where we want to fill gaps without
    overwriting daily-scraped values.
    """
    cur = conn.execute("""
        INSERT OR IGNORE INTO runoff_observations (
            site_id, site_name, obs_date, water_year, scraped_at, row_type,
            oct, nov, dec, jan, feb, mar, apr, may, jun, jul, aug, sep,
            cumul_oct_to_date, cumul_jan_to_date, cumul_apr_to_date
        ) VALUES (
            :site_id, :site_name, :obs_date, :water_year, :scraped_at, :row_type,
            :oct, :nov, :dec, :jan, :feb, :mar, :apr, :may, :jun, :jul, :aug, :sep,
            :cumul_oct_to_date, :cumul_jan_to_date, :cumul_apr_to_date
        )
    """, record)
    return cur.rowcount > 0


def upsert_observation(conn: sqlite3.Connection, record: dict) -> None:
    """Insert or update a single observation record."""
    conn.execute("""
        INSERT INTO runoff_observations (
            site_id, site_name, obs_date, water_year, scraped_at, row_type,
            oct, nov, dec, jan, feb, mar, apr, may, jun, jul, aug, sep,
            cumul_oct_to_date, cumul_jan_to_date, cumul_apr_to_date
        ) VALUES (
            :site_id, :site_name, :obs_date, :water_year, :scraped_at, :row_type,
            :oct, :nov, :dec, :jan, :feb, :mar, :apr, :may, :jun, :jul, :aug, :sep,
            :cumul_oct_to_date, :cumul_jan_to_date, :cumul_apr_to_date
        )
        ON CONFLICT(site_id, obs_date, row_type) DO UPDATE SET
            site_name           = excluded.site_name,
            scraped_at          = excluded.scraped_at,
            oct                 = excluded.oct,
            nov                 = excluded.nov,
            dec                 = excluded.dec,
            jan                 = excluded.jan,
            feb                 = excluded.feb,
            mar                 = excluded.mar,
            apr                 = excluded.apr,
            may                 = excluded.may,
            jun                 = excluded.jun,
            jul                 = excluded.jul,
            aug                 = excluded.aug,
            sep                 = excluded.sep,
            cumul_oct_to_date   = excluded.cumul_oct_to_date,
            cumul_jan_to_date   = excluded.cumul_jan_to_date,
            cumul_apr_to_date   = excluded.cumul_apr_to_date
    """, record)


def log_scrape(conn: sqlite3.Connection, run_at: str, obs_date: str | None,
               records_in: int, status: str, message: str | None = None) -> None:
    conn.execute("""
        INSERT INTO scrape_log (run_at, obs_date, records_in, status, message)
        VALUES (?, ?, ?, ?, ?)
    """, (run_at, obs_date, records_in, status, message))
    conn.commit()
