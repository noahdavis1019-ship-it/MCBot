"""SQLite database schema and operations for EXP-001."""

import sqlite3
from pathlib import Path

from mcbot.timeutil import utcnow_iso

SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

-- Migration events from PumpPortal
CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    symbol TEXT,
    pool TEXT,
    migration_ts_utc TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'pumpportal',
    raw_payload TEXT NOT NULL,
    collected_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_migrations_mint ON migrations(mint);
CREATE INDEX IF NOT EXISTS idx_migrations_ts ON migrations(migration_ts_utc);

-- Price/liquidity observations at scheduled horizons
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    horizon_label TEXT NOT NULL,
    scheduled_ts_utc TEXT NOT NULL,
    actual_ts_utc TEXT NOT NULL,
    obs_status TEXT NOT NULL DEFAULT 'OK',
    price_usd REAL,
    price_native REAL,
    liquidity_usd REAL,
    fdv REAL,
    vol_5m REAL,
    vol_1h REAL,
    txns_buys_5m INTEGER,
    txns_sells_5m INTEGER,
    dex_id TEXT,
    http_status INTEGER,
    request_latency_ms INTEGER,
    raw_payload TEXT,
    collected_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_mint ON observations(mint);
CREATE INDEX IF NOT EXISTS idx_observations_horizon ON observations(horizon_label);
CREATE INDEX IF NOT EXISTS idx_observations_scheduled ON observations(scheduled_ts_utc);
CREATE INDEX IF NOT EXISTS idx_observations_status ON observations(obs_status);

-- Jupiter quote probes for execution cost estimation
CREATE TABLE IF NOT EXISTS quote_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    probe_ts_utc TEXT NOT NULL,
    direction TEXT NOT NULL,
    in_amount_lamports INTEGER NOT NULL,
    out_amount INTEGER,
    price_impact_pct REAL,
    route_plan_json TEXT,
    slippage_bps_requested INTEGER NOT NULL DEFAULT 100,
    http_status INTEGER NOT NULL,
    request_latency_ms INTEGER NOT NULL,
    raw_payload TEXT,
    collected_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quote_probes_mint ON quote_probes(mint);
CREATE INDEX IF NOT EXISTS idx_quote_probes_ts ON quote_probes(probe_ts_utc);
CREATE INDEX IF NOT EXISTS idx_quote_probes_direction ON quote_probes(direction);

-- Parse failures for schema drift detection
CREATE TABLE IF NOT EXISTS parse_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts TEXT NOT NULL,
    raw_frame TEXT NOT NULL,
    reason TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    collected_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parse_failures_ts ON parse_failures(received_ts);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema and return connection.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLite connection with WAL mode enabled
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Apply schema
    conn.executescript(SCHEMA_SQL)

    # Record schema version if not exists
    cursor = conn.execute("SELECT version FROM schema_version WHERE version = ?", (SCHEMA_VERSION,))
    if cursor.fetchone() is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at_utc) VALUES (?, ?)",
            (SCHEMA_VERSION, utcnow_iso())
        )
        conn.commit()

    return conn


def insert_migration(
    conn: sqlite3.Connection,
    mint: str,
    symbol: str | None,
    pool: str | None,
    migration_ts_utc: str,
    raw_payload: str,
) -> int:
    """Insert a migration event.

    Args:
        conn: SQLite connection
        mint: Token mint address
        symbol: Token symbol/ticker (may be None)
        pool: Raydium pool address (may be None)
        migration_ts_utc: Migration timestamp from payload
        raw_payload: Full JSON payload as string

    Returns:
        Row ID of inserted migration
    """
    cursor = conn.execute(
        """
        INSERT INTO migrations (mint, symbol, pool, migration_ts_utc, raw_payload, collected_at_utc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (mint, symbol, pool, migration_ts_utc, raw_payload, utcnow_iso())
    )
    conn.commit()
    return cursor.lastrowid


def insert_observation(
    conn: sqlite3.Connection,
    mint: str,
    horizon_label: str,
    scheduled_ts_utc: str,
    actual_ts_utc: str,
    obs_status: str = "OK",
    price_usd: float | None = None,
    price_native: float | None = None,
    liquidity_usd: float | None = None,
    fdv: float | None = None,
    vol_5m: float | None = None,
    vol_1h: float | None = None,
    txns_buys_5m: int | None = None,
    txns_sells_5m: int | None = None,
    dex_id: str | None = None,
    http_status: int | None = None,
    request_latency_ms: int | None = None,
    raw_payload: str | None = None,
) -> int:
    """Insert an observation (or heartbeat if http_status is None).

    Args:
        conn: SQLite connection
        mint: Token mint address
        horizon_label: Horizon name (1m, 5m, 15m, 30m, 1h, 4h, 24h)
        scheduled_ts_utc: When observation was scheduled
        actual_ts_utc: When observation was attempted
        obs_status: Observation status (OK | MISSED_LATE | HTTP_ERROR | PARSE_ERROR)
        price_usd: Token price in USD
        price_native: Token price in native (SOL)
        liquidity_usd: Pool liquidity in USD
        fdv: Fully diluted valuation in USD
        vol_5m: 5-minute volume in USD
        vol_1h: 1-hour volume in USD
        txns_buys_5m: Buy transaction count (5m)
        txns_sells_5m: Sell transaction count (5m)
        dex_id: DEX identifier
        http_status: HTTP status code (real HTTP codes only, not sentinels)
        request_latency_ms: Request latency in ms
        raw_payload: Full JSON response as string

    Returns:
        Row ID of inserted observation
    """
    cursor = conn.execute(
        """
        INSERT INTO observations (
            mint, horizon_label, scheduled_ts_utc, actual_ts_utc, obs_status,
            price_usd, price_native, liquidity_usd, fdv,
            vol_5m, vol_1h, txns_buys_5m, txns_sells_5m,
            dex_id, http_status, request_latency_ms, raw_payload,
            collected_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mint, horizon_label, scheduled_ts_utc, actual_ts_utc, obs_status,
            price_usd, price_native, liquidity_usd, fdv,
            vol_5m, vol_1h, txns_buys_5m, txns_sells_5m,
            dex_id, http_status, request_latency_ms, raw_payload,
            utcnow_iso()
        )
    )
    conn.commit()
    return cursor.lastrowid


def insert_quote_probe(
    conn: sqlite3.Connection,
    mint: str,
    probe_ts_utc: str,
    direction: str,
    in_amount_lamports: int,
    out_amount: int | None,
    price_impact_pct: float | None,
    route_plan_json: str | None,
    http_status: int,
    request_latency_ms: int,
    raw_payload: str | None,
    slippage_bps_requested: int = 100,
) -> int:
    """Insert a Jupiter quote probe.

    Args:
        conn: SQLite connection
        mint: Token mint address
        probe_ts_utc: When quote was requested
        direction: "SOL->TOKEN" or "TOKEN->SOL"
        in_amount_lamports: Input amount in lamports
        out_amount: Output amount quoted (in lamports or token units)
        price_impact_pct: Price impact percentage
        route_plan_json: Full route plan as JSON string
        http_status: HTTP status code
        request_latency_ms: Request latency in ms
        raw_payload: Full JSON response as string
        slippage_bps_requested: Slippage tolerance in basis points

    Returns:
        Row ID of inserted quote probe
    """
    cursor = conn.execute(
        """
        INSERT INTO quote_probes (
            mint, probe_ts_utc, direction, in_amount_lamports,
            out_amount, price_impact_pct, route_plan_json,
            slippage_bps_requested, http_status, request_latency_ms,
            raw_payload, collected_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mint, probe_ts_utc, direction, in_amount_lamports,
            out_amount, price_impact_pct, route_plan_json,
            slippage_bps_requested, http_status, request_latency_ms,
            raw_payload, utcnow_iso()
        )
    )
    conn.commit()
    return cursor.lastrowid


def insert_heartbeat(conn: sqlite3.Connection) -> int:
    """Insert a heartbeat observation row.

    Heartbeat rows have http_status=NULL and are used to detect downtime.

    Args:
        conn: SQLite connection

    Returns:
        Row ID of inserted heartbeat
    """
    now = utcnow_iso()
    return insert_observation(
        conn=conn,
        mint="HEARTBEAT",
        horizon_label="heartbeat",
        scheduled_ts_utc=now,
        actual_ts_utc=now,
        http_status=None,
    )


def insert_parse_failure(
    conn: sqlite3.Connection,
    received_ts: str,
    raw_frame: str,
    reason: str,
    parser_version: str = "1",
) -> int:
    """Insert a parse failure for schema drift detection.

    Args:
        conn: SQLite connection
        received_ts: When frame was received
        raw_frame: Raw websocket message
        reason: Why parsing failed
        parser_version: Version of parser that failed

    Returns:
        Row ID of inserted failure
    """
    cursor = conn.execute(
        """
        INSERT INTO parse_failures (received_ts, raw_frame, reason, parser_version, collected_at_utc)
        VALUES (?, ?, ?, ?, ?)
        """,
        (received_ts, raw_frame, reason, parser_version, utcnow_iso())
    )
    conn.commit()
    return cursor.lastrowid


def update_observation_status(
    conn: sqlite3.Connection,
    observation_id: int,
    actual_ts_utc: str,
    obs_status: str,
    price_usd: float | None = None,
    price_native: float | None = None,
    liquidity_usd: float | None = None,
    fdv: float | None = None,
    vol_5m: float | None = None,
    vol_1h: float | None = None,
    txns_buys_5m: int | None = None,
    txns_sells_5m: int | None = None,
    dex_id: str | None = None,
    http_status: int | None = None,
    request_latency_ms: int | None = None,
    raw_payload: str | None = None,
) -> None:
    """Update observation status and data after execution.

    This is the ONLY allowed UPDATE operation - transitioning from PENDING → final state.
    Documented exception to append-only design for data loss prevention.

    Args:
        conn: SQLite connection
        observation_id: Row ID of observation to update
        actual_ts_utc: When observation was actually attempted
        obs_status: Final status (OK | MISSED_LATE | HTTP_ERROR | PARSE_ERROR)
        price_usd: Token price in USD
        price_native: Token price in native (SOL)
        liquidity_usd: Pool liquidity in USD
        fdv: Fully diluted valuation
        vol_5m: 5-minute volume
        vol_1h: 1-hour volume
        txns_buys_5m: Buy transaction count (5m)
        txns_sells_5m: Sell transaction count (5m)
        dex_id: DEX identifier
        http_status: HTTP status code (real codes only, not sentinels)
        request_latency_ms: Request latency in ms
        raw_payload: Full JSON response as string
    """
    conn.execute(
        """
        UPDATE observations
        SET actual_ts_utc = ?,
            obs_status = ?,
            price_usd = ?,
            price_native = ?,
            liquidity_usd = ?,
            fdv = ?,
            vol_5m = ?,
            vol_1h = ?,
            txns_buys_5m = ?,
            txns_sells_5m = ?,
            dex_id = ?,
            http_status = ?,
            request_latency_ms = ?,
            raw_payload = ?
        WHERE id = ?
        """,
        (
            actual_ts_utc, obs_status,
            price_usd, price_native, liquidity_usd, fdv,
            vol_5m, vol_1h, txns_buys_5m, txns_sells_5m,
            dex_id, http_status, request_latency_ms, raw_payload,
            observation_id
        )
    )
    conn.commit()


def load_pending_observations(conn: sqlite3.Connection) -> list[dict]:
    """Load all PENDING observations from database.

    Returns list ordered by scheduled_ts_utc ascending (earliest first).

    Args:
        conn: SQLite connection

    Returns:
        List of dicts with fields:
            - id: Observation row ID
            - mint: Token mint address
            - horizon_label: Horizon name
            - scheduled_ts_utc: When observation should run (ISO 8601)
            - migration_ts_utc: Migration timestamp (derived from first observation)
    """
    cursor = conn.execute(
        """
        SELECT id, mint, horizon_label, scheduled_ts_utc
        FROM observations
        WHERE obs_status = 'PENDING'
        ORDER BY scheduled_ts_utc ASC
        """
    )

    rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "mint": row[1],
            "horizon_label": row[2],
            "scheduled_ts_utc": row[3],
        }
        for row in rows
    ]


def expire_overdue_pending_observations(conn: sqlite3.Connection, cutoff_ts_utc: str) -> int:
    """Mark PENDING observations that are >5 min overdue as MISSED_LATE.

    This handles observations that were pending when collector was killed.

    Args:
        conn: SQLite connection
        cutoff_ts_utc: ISO 8601 UTC timestamp - observations scheduled before this are expired

    Returns:
        Number of observations expired
    """
    from mcbot.timeutil import utcnow_iso

    actual_ts_utc = utcnow_iso()

    cursor = conn.execute(
        """
        UPDATE observations
        SET actual_ts_utc = ?,
            obs_status = 'MISSED_LATE',
            raw_payload = 'Observation expired due to restart gap (>5 min overdue at startup)'
        WHERE obs_status = 'PENDING'
          AND scheduled_ts_utc < ?
        """,
        (actual_ts_utc, cutoff_ts_utc)
    )

    conn.commit()
    return cursor.rowcount


def get_coverage_report(conn: sqlite3.Connection, start_ts_utc: str | None = None, end_ts_utc: str | None = None) -> list[dict]:
    """Get hourly coverage report showing uptime and observation counts.

    Args:
        conn: SQLite connection
        start_ts_utc: Start of reporting period (ISO 8601 UTC), defaults to 24h ago
        end_ts_utc: End of reporting period (ISO 8601 UTC), defaults to now

    Returns:
        List of dicts with hourly statistics:
            - hour_utc: Hour bucket (YYYY-MM-DD HH:00:00)
            - heartbeats_expected: Always 12 (one every 5 minutes)
            - heartbeats_received: Actual heartbeat count
            - uptime_pct: (received / expected) * 100
            - migrations: Count of migration events
            - obs_ok: Observations with status OK
            - obs_missed: Observations with status MISSED_LATE
            - obs_error: Observations with status HTTP_ERROR
    """
    if not start_ts_utc:
        # Default to 24 hours ago
        from datetime import datetime, timedelta, timezone as tz
        start_ts_utc = (datetime.now(tz.utc) - timedelta(hours=24)).isoformat()

    if not end_ts_utc:
        end_ts_utc = utcnow_iso()

    # Query for hourly statistics
    query = """
    WITH hours AS (
        -- Generate hourly buckets
        SELECT DISTINCT
            strftime('%Y-%m-%d %H:00:00', actual_ts_utc) as hour_utc
        FROM observations
        WHERE actual_ts_utc >= ? AND actual_ts_utc < ?
        UNION
        SELECT DISTINCT
            strftime('%Y-%m-%d %H:00:00', migration_ts_utc) as hour_utc
        FROM migrations
        WHERE migration_ts_utc >= ? AND migration_ts_utc < ?
    ),
    heartbeats AS (
        SELECT
            strftime('%Y-%m-%d %H:00:00', actual_ts_utc) as hour_utc,
            COUNT(*) as received
        FROM observations
        WHERE horizon_label = 'heartbeat'
          AND actual_ts_utc >= ? AND actual_ts_utc < ?
        GROUP BY hour_utc
    ),
    migration_counts AS (
        SELECT
            strftime('%Y-%m-%d %H:00:00', migration_ts_utc) as hour_utc,
            COUNT(*) as count
        FROM migrations
        WHERE migration_ts_utc >= ? AND migration_ts_utc < ?
        GROUP BY hour_utc
    ),
    obs_stats AS (
        SELECT
            strftime('%Y-%m-%d %H:00:00', actual_ts_utc) as hour_utc,
            SUM(CASE WHEN obs_status = 'OK' THEN 1 ELSE 0 END) as ok,
            SUM(CASE WHEN obs_status = 'MISSED_LATE' THEN 1 ELSE 0 END) as missed,
            SUM(CASE WHEN obs_status = 'HTTP_ERROR' THEN 1 ELSE 0 END) as error
        FROM observations
        WHERE horizon_label != 'heartbeat'
          AND actual_ts_utc >= ? AND actual_ts_utc < ?
        GROUP BY hour_utc
    )
    SELECT
        h.hour_utc,
        12 as heartbeats_expected,
        COALESCE(hb.received, 0) as heartbeats_received,
        ROUND(COALESCE(hb.received, 0) * 100.0 / 12, 1) as uptime_pct,
        COALESCE(m.count, 0) as migrations,
        COALESCE(o.ok, 0) as obs_ok,
        COALESCE(o.missed, 0) as obs_missed,
        COALESCE(o.error, 0) as obs_error
    FROM hours h
    LEFT JOIN heartbeats hb ON h.hour_utc = hb.hour_utc
    LEFT JOIN migration_counts m ON h.hour_utc = m.hour_utc
    LEFT JOIN obs_stats o ON h.hour_utc = o.hour_utc
    ORDER BY h.hour_utc
    """

    cursor = conn.execute(query, (
        start_ts_utc, end_ts_utc,  # hours from observations
        start_ts_utc, end_ts_utc,  # hours from migrations
        start_ts_utc, end_ts_utc,  # heartbeats
        start_ts_utc, end_ts_utc,  # migrations count
        start_ts_utc, end_ts_utc,  # obs_stats
    ))

    rows = cursor.fetchall()
    return [
        {
            "hour_utc": row[0],
            "heartbeats_expected": row[1],
            "heartbeats_received": row[2],
            "uptime_pct": row[3],
            "migrations": row[4],
            "obs_ok": row[5],
            "obs_missed": row[6],
            "obs_error": row[7],
        }
        for row in rows
    ]
