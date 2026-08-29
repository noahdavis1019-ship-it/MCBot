"""Test that missed/late observations are logged correctly."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mcbot.db import init_db
from mcbot.ratelimit import RateLimiter
from mcbot.scheduler import ObservationScheduler


@pytest.mark.asyncio
async def test_late_observation_is_logged_as_missed():
    """Test that observations >5 minutes late are logged with 429 status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()
        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule an observation far in the past
        # Migration 30 min ago + 1 min horizon = observation scheduled for 29 min ago
        old_migration_ts = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        scheduler.schedule_observations("test_mint", old_migration_ts)

        # Verify observations were queued
        assert len(scheduler._queue) > 0, "No observations were queued"

        # Process queue multiple times to handle all late observations
        for _ in range(10):  # Process up to 10 observations
            await scheduler._process_queue()
            if len(scheduler._queue) == 0:
                break

        # Check that missed observations were logged
        cursor = db.execute(
            "SELECT http_status, raw_payload, horizon_label FROM observations WHERE mint = ?",
            ("test_mint",)
        )
        rows = cursor.fetchall()

        # Should have multiple observations logged as rate limited
        missed = [r for r in rows if r[0] == 429]
        assert len(missed) > 0, f"No missed observations logged. All rows: {rows}, Queue size: {len(scheduler._queue)}"

        # Check the payload mentions rate limits and lateness
        payload = missed[0][1]
        assert "skipped" in payload.lower() or "late" in payload.lower()
        assert "rate limit" in payload.lower()


@pytest.mark.asyncio
async def test_slightly_late_observation_is_deferred():
    """Test that observations <5 minutes late are deferred, not skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        # Exhaust rate limiter
        for _ in range(10):
            rate_limiter.try_acquire_dexscreener()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule an observation 2 minutes in the past (late but not extreme)
        recent_migration_ts = (datetime.utcnow() - timedelta(minutes=2)).isoformat()
        scheduler.schedule_observations("test_mint", recent_migration_ts)

        # Process queue - should defer, not skip
        await scheduler._process_queue()

        # Should NOT have logged a 429 missed observation
        cursor = db.execute(
            "SELECT http_status FROM observations WHERE mint = ? AND http_status = 429",
            ("test_mint",)
        )
        rows = cursor.fetchall()
        assert len(rows) == 0, "Observation was incorrectly marked as missed"

        # Queue should still contain the observation (deferred)
        assert len(scheduler._queue) > 0, "Observation was not deferred"


@pytest.mark.asyncio
async def test_missed_observation_fields():
    """Test that missed observation has correct fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()
        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule very late observation (30 min ago)
        old_migration_ts = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        scheduler.schedule_observations("test_mint", old_migration_ts)

        await scheduler._process_queue()

        # Verify fields
        cursor = db.execute(
            """
            SELECT mint, horizon_label, http_status, request_latency_ms,
                   price_usd, liquidity_usd, raw_payload
            FROM observations WHERE http_status = 429
            LIMIT 1
            """,
        )
        row = cursor.fetchone()

        assert row is not None, "No missed observation found"
        assert row[0] == "test_mint"  # mint
        assert row[1] in ["1m", "5m", "15m", "30m", "1h", "4h", "24h"]  # horizon_label
        assert row[2] == 429  # http_status
        assert row[3] == 0  # request_latency_ms
        assert row[4] is None  # price_usd (should be NULL)
        assert row[5] is None  # liquidity_usd (should be NULL)
        assert "skipped" in row[6].lower()  # raw_payload
