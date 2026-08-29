"""Tests for coverage reporting."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcbot.db import get_coverage_report, init_db, insert_heartbeat, insert_migration, insert_observation


def test_coverage_report_empty():
    """Test coverage report with no data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Query with no data
        rows = get_coverage_report(db)

        assert rows == []


def test_coverage_report_with_heartbeats():
    """Test coverage report counts heartbeats correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert heartbeats in a specific hour
        base_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Insert 10 heartbeats in the hour
        for i in range(10):
            ts = (base_time + timedelta(minutes=i * 5)).isoformat()
            insert_observation(
                db,
                mint="HEARTBEAT",
                horizon_label="heartbeat",
                scheduled_ts_utc=ts,
                actual_ts_utc=ts,
        obs_status="OK",
                http_status=None,
            )

        # Query coverage
        start_ts = base_time.isoformat()
        end_ts = (base_time + timedelta(hours=2)).isoformat()
        rows = get_coverage_report(db, start_ts, end_ts)

        assert len(rows) == 1
        assert rows[0]["heartbeats_expected"] == 12
        assert rows[0]["heartbeats_received"] == 10
        assert rows[0]["uptime_pct"] == 83.3  # 10/12 * 100 = 83.3


def test_coverage_report_with_migrations():
    """Test coverage report counts migrations correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        base_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Insert migrations
        for i in range(3):
            ts = (base_time + timedelta(minutes=i * 10)).isoformat()
            insert_migration(
                db,
                mint=f"mint{i}",
                symbol=None,
                pool="pump-amm",
                migration_ts_utc=ts,
                raw_payload="{}",
            )

        # Query coverage
        start_ts = base_time.isoformat()
        end_ts = (base_time + timedelta(hours=2)).isoformat()
        rows = get_coverage_report(db, start_ts, end_ts)

        assert len(rows) == 1
        assert rows[0]["migrations"] == 3


def test_coverage_report_with_observations():
    """Test coverage report counts observations by status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        base_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Insert observations with different statuses
        for i in range(5):
            ts = (base_time + timedelta(minutes=i)).isoformat()
            insert_observation(
                db,
                mint="test_mint",
                horizon_label="5m",
                scheduled_ts_utc=ts,
                actual_ts_utc=ts,
                obs_status="OK",
                http_status=200,
            )

        for i in range(2):
            ts = (base_time + timedelta(minutes=i + 10)).isoformat()
            insert_observation(
                db,
                mint="test_mint",
                horizon_label="5m",
                scheduled_ts_utc=ts,
                actual_ts_utc=ts,
                obs_status="MISSED_LATE",
                http_status=None,
            )

        ts = (base_time + timedelta(minutes=20)).isoformat()
        insert_observation(
            db,
            mint="test_mint",
            horizon_label="5m",
            scheduled_ts_utc=ts,
            actual_ts_utc=ts,
            obs_status="HTTP_ERROR",
            http_status=500,
        )

        # Query coverage
        start_ts = base_time.isoformat()
        end_ts = (base_time + timedelta(hours=2)).isoformat()
        rows = get_coverage_report(db, start_ts, end_ts)

        assert len(rows) == 1
        assert rows[0]["obs_ok"] == 5
        assert rows[0]["obs_missed"] == 2
        assert rows[0]["obs_error"] == 1


def test_coverage_report_multiple_hours():
    """Test coverage report with data spanning multiple hours."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        base_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Insert data in hour 1
        ts1 = base_time.isoformat()
        insert_migration(db, mint="mint1", symbol=None, pool="pump-amm", migration_ts_utc=ts1, raw_payload="{}")

        # Insert data in hour 2
        ts2 = (base_time + timedelta(hours=1)).isoformat()
        insert_migration(db, mint="mint2", symbol=None, pool="pump-amm", migration_ts_utc=ts2, raw_payload="{}")
        insert_migration(db, mint="mint3", symbol=None, pool="pump-amm", migration_ts_utc=ts2, raw_payload="{}")

        # Query coverage
        start_ts = base_time.isoformat()
        end_ts = (base_time + timedelta(hours=3)).isoformat()
        rows = get_coverage_report(db, start_ts, end_ts)

        assert len(rows) == 2
        assert rows[0]["migrations"] == 1
        assert rows[1]["migrations"] == 2
