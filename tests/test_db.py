"""Unit tests for database schema and operations."""

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from mcbot.db import (
    init_db,
    insert_heartbeat,
    insert_migration,
    insert_observation,
    insert_quote_probe,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


def test_init_db_creates_schema(temp_db):
    """Test that init_db creates all tables and indexes."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]

    assert "migrations" in tables
    assert "observations" in tables
    assert "quote_probes" in tables
    assert "schema_version" in tables


def test_init_db_sets_wal_mode(temp_db):
    """Test that init_db enables WAL mode."""
    cursor = temp_db.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"


def test_schema_version_recorded(temp_db):
    """Test that schema version is recorded."""
    cursor = temp_db.execute("SELECT version FROM schema_version")
    version = cursor.fetchone()[0]
    assert version == 1


def test_insert_migration(temp_db):
    """Test migration insertion and retrieval."""
    row_id = insert_migration(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        symbol="PUMP",
        pool="PoolAddress123",
        migration_ts_utc="2026-08-29T12:00:00",
        raw_payload='{"mint": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"}',
    )

    assert row_id > 0

    # Verify data
    cursor = temp_db.execute("SELECT * FROM migrations WHERE id = ?", (row_id,))
    row = cursor.fetchone()

    assert row[1] == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # mint
    assert row[2] == "PUMP"  # symbol
    assert row[3] == "PoolAddress123"  # pool
    assert row[4] == "2026-08-29T12:00:00"  # migration_ts_utc
    assert row[5] == "pumpportal"  # source
    assert "mint" in row[6]  # raw_payload


def test_insert_migration_nullable_fields(temp_db):
    """Test migration with null symbol and pool."""
    row_id = insert_migration(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        symbol=None,
        pool=None,
        migration_ts_utc="2026-08-29T12:00:00",
        raw_payload="{}",
    )

    cursor = temp_db.execute("SELECT symbol, pool FROM migrations WHERE id = ?", (row_id,))
    row = cursor.fetchone()

    assert row[0] is None  # symbol
    assert row[1] is None  # pool


def test_insert_observation_success(temp_db):
    """Test successful observation insertion."""
    row_id = insert_observation(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        horizon_label="5m",
        scheduled_ts_utc="2026-08-29T12:05:00",
        actual_ts_utc="2026-08-29T12:05:02",
        price_usd=0.0001234,
        price_native=0.000000567,
        liquidity_usd=50000.0,
        fdv=123456.78,
        vol_5m=1000.0,
        vol_1h=5000.0,
        txns_buys_5m=10,
        txns_sells_5m=8,
        dex_id="raydium",
        http_status=200,
        request_latency_ms=350,
        raw_payload='{"pairs": []}',
    )

    assert row_id > 0

    # Verify data
    cursor = temp_db.execute("SELECT * FROM observations WHERE id = ?", (row_id,))
    row = cursor.fetchone()

    assert row[1] == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # mint
    assert row[2] == "5m"  # horizon_label
    assert row[5] == 0.0001234  # price_usd
    assert row[11] == 10  # txns_buys_5m
    assert row[14] == 200  # http_status


def test_insert_observation_failed(temp_db):
    """Test failed observation (HTTP error)."""
    row_id = insert_observation(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        horizon_label="1h",
        scheduled_ts_utc="2026-08-29T13:00:00",
        actual_ts_utc="2026-08-29T13:00:05",
        http_status=404,
        request_latency_ms=200,
        raw_payload="Not found",
    )

    cursor = temp_db.execute(
        "SELECT price_usd, http_status FROM observations WHERE id = ?",
        (row_id,)
    )
    row = cursor.fetchone()

    assert row[0] is None  # price_usd should be NULL
    assert row[1] == 404  # http_status


def test_insert_heartbeat(temp_db):
    """Test heartbeat insertion."""
    row_id = insert_heartbeat(temp_db)

    cursor = temp_db.execute("SELECT mint, http_status FROM observations WHERE id = ?", (row_id,))
    row = cursor.fetchone()

    assert row[0] == "HEARTBEAT"  # mint
    assert row[1] is None  # http_status


def test_insert_quote_probe_success(temp_db):
    """Test successful quote probe insertion."""
    row_id = insert_quote_probe(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        probe_ts_utc="2026-08-29T12:01:00",
        direction="SOL->TOKEN",
        in_amount_lamports=100_000_000,
        out_amount=500_000_000,
        price_impact_pct=1.5,
        route_plan_json='[{"swap": "raydium"}]',
        http_status=200,
        request_latency_ms=450,
        raw_payload='{"outAmount": "500000000"}',
    )

    assert row_id > 0

    cursor = temp_db.execute("SELECT * FROM quote_probes WHERE id = ?", (row_id,))
    row = cursor.fetchone()

    assert row[1] == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # mint
    assert row[3] == "SOL->TOKEN"  # direction
    assert row[4] == 100_000_000  # in_amount_lamports
    assert row[5] == 500_000_000  # out_amount
    assert row[6] == 1.5  # price_impact_pct
    assert row[8] == 100  # slippage_bps_requested


def test_insert_quote_probe_failed(temp_db):
    """Test failed quote probe (HTTP error)."""
    row_id = insert_quote_probe(
        conn=temp_db,
        mint="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        probe_ts_utc="2026-08-29T12:01:00",
        direction="TOKEN->SOL",
        in_amount_lamports=1_000_000,
        out_amount=None,
        price_impact_pct=None,
        route_plan_json=None,
        http_status=500,
        request_latency_ms=100,
        raw_payload="Internal server error",
    )

    cursor = temp_db.execute(
        "SELECT out_amount, price_impact_pct, http_status FROM quote_probes WHERE id = ?",
        (row_id,)
    )
    row = cursor.fetchone()

    assert row[0] is None  # out_amount
    assert row[1] is None  # price_impact_pct
    assert row[2] == 500  # http_status


def test_migration_index_exists(temp_db):
    """Test that migration indexes exist."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='migrations'"
    )
    indexes = [row[0] for row in cursor.fetchall()]

    assert "idx_migrations_mint" in indexes
    assert "idx_migrations_ts" in indexes


def test_observation_index_exists(temp_db):
    """Test that observation indexes exist."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='observations'"
    )
    indexes = [row[0] for row in cursor.fetchall()]

    assert "idx_observations_mint" in indexes
    assert "idx_observations_horizon" in indexes
    assert "idx_observations_scheduled" in indexes


def test_quote_probe_index_exists(temp_db):
    """Test that quote probe indexes exist."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='quote_probes'"
    )
    indexes = [row[0] for row in cursor.fetchall()]

    assert "idx_quote_probes_mint" in indexes
    assert "idx_quote_probes_ts" in indexes
    assert "idx_quote_probes_direction" in indexes
