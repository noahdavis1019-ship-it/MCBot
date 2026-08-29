"""SQLite database schema and operations for EXP-001."""

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1

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
            (SCHEMA_VERSION, datetime.utcnow().isoformat())
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
        (mint, symbol, pool, migration_ts_utc, raw_payload, datetime.utcnow().isoformat())
    )
    conn.commit()
    return cursor.lastrowid


def insert_observation(
    conn: sqlite3.Connection,
    mint: str,
    horizon_label: str,
    scheduled_ts_utc: str,
    actual_ts_utc: str,
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
        price_usd: Token price in USD
        price_native: Token price in native (SOL)
        liquidity_usd: Pool liquidity in USD
        fdv: Fully diluted valuation in USD
        vol_5m: 5-minute volume in USD
        vol_1h: 1-hour volume in USD
        txns_buys_5m: Buy transaction count (5m)
        txns_sells_5m: Sell transaction count (5m)
        dex_id: DEX identifier
        http_status: HTTP status code (None for heartbeat)
        request_latency_ms: Request latency in ms
        raw_payload: Full JSON response as string

    Returns:
        Row ID of inserted observation
    """
    cursor = conn.execute(
        """
        INSERT INTO observations (
            mint, horizon_label, scheduled_ts_utc, actual_ts_utc,
            price_usd, price_native, liquidity_usd, fdv,
            vol_5m, vol_1h, txns_buys_5m, txns_sells_5m,
            dex_id, http_status, request_latency_ms, raw_payload,
            collected_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mint, horizon_label, scheduled_ts_utc, actual_ts_utc,
            price_usd, price_native, liquidity_usd, fdv,
            vol_5m, vol_1h, txns_buys_5m, txns_sells_5m,
            dex_id, http_status, request_latency_ms, raw_payload,
            datetime.utcnow().isoformat()
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
            raw_payload, datetime.utcnow().isoformat()
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
    now = datetime.utcnow().isoformat()
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
        (received_ts, raw_frame, reason, parser_version, datetime.utcnow().isoformat())
    )
    conn.commit()
    return cursor.lastrowid
