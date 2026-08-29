"""Tests for observation queue persistence and restart recovery."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcbot.db import init_db, load_pending_observations
from mcbot.ratelimit import RateLimiter
from mcbot.scheduler import ObservationScheduler


def test_pending_observations_survive_restart():
    """Test that PENDING observations survive collector restart.

    This is the acceptance gate for 24h run. Tests:
    1. Observations inserted as PENDING at migration time
    2. Restart loads PENDING observations back into queue
    3. Overdue observations (>5 min) marked MISSED_LATE
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        # Create first scheduler instance (pre-restart)
        scheduler1 = ObservationScheduler(db, rate_limiter)

        # Schedule observations for a migration
        migration_ts = datetime.now(timezone.utc).isoformat()
        scheduler1.schedule_observations("test_mint", migration_ts)

        # Verify 7 observations in queue
        assert len(scheduler1._queue) == 7

        # Verify all 7 observations in DB with PENDING status
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 7

        # Simulate restart by creating new scheduler instance
        # This destroys the in-memory queue (simulates crash/restart)
        scheduler2 = ObservationScheduler(db, rate_limiter)

        # Before calling start(), queue should be empty
        assert len(scheduler2._queue) == 0

        # Call load_pending_observations (called by start())
        scheduler2.load_pending_observations()

        # Verify queue rebuilt from DB
        assert len(scheduler2._queue) == 7

        # Verify all observations still PENDING (not overdue yet)
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 7


def test_overdue_pending_observations_expired_on_restart():
    """Test that overdue PENDING observations are expired on restart."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule observations for a migration 25 hours ago
        # This ensures ALL horizons (1m through 24h) are >5 min overdue
        migration_ts_past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        scheduler.schedule_observations("test_mint_past", migration_ts_past)

        # All observations should be PENDING
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 7

        # Simulate restart - load pending observations
        scheduler2 = ObservationScheduler(db, rate_limiter)
        scheduler2.load_pending_observations()

        # All observations from 25h ago should be expired (>5 min overdue)
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'MISSED_LATE'")
        missed_count = cursor.fetchone()[0]
        assert missed_count == 7

        # No observations should remain PENDING
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 0

        # Queue should be empty (no overdue observations loaded)
        assert len(scheduler2._queue) == 0


def test_mixed_pending_and_overdue_observations():
    """Test restart with mix of current and overdue observations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule observations for a migration 25 hours ago (will be expired)
        migration_ts_past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        scheduler.schedule_observations("overdue_mint", migration_ts_past)

        # Schedule observations for a migration now (will be loaded)
        migration_ts_now = datetime.now(timezone.utc).isoformat()
        scheduler.schedule_observations("current_mint", migration_ts_now)

        # Should have 14 PENDING observations (7 + 7)
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 14

        # Simulate restart
        scheduler2 = ObservationScheduler(db, rate_limiter)
        scheduler2.load_pending_observations()

        # 7 overdue observations should be expired
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'MISSED_LATE'")
        missed_count = cursor.fetchone()[0]
        assert missed_count == 7

        # 7 current observations should still be PENDING
        cursor = db.execute("SELECT COUNT(*) FROM observations WHERE obs_status = 'PENDING'")
        pending_count = cursor.fetchone()[0]
        assert pending_count == 7

        # Queue should have 7 observations (current only)
        assert len(scheduler2._queue) == 7


def test_load_pending_preserves_observation_ids():
    """Test that reloaded observations retain their database IDs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler1 = ObservationScheduler(db, rate_limiter)

        # Schedule observations
        migration_ts = datetime.now(timezone.utc).isoformat()
        scheduler1.schedule_observations("test_mint", migration_ts)

        # Collect original observation IDs
        original_ids = {obs.obs_id for obs in scheduler1._queue}
        assert len(original_ids) == 7
        assert 0 not in original_ids  # No obs_id should be 0

        # Simulate restart
        scheduler2 = ObservationScheduler(db, rate_limiter)
        scheduler2.load_pending_observations()

        # Collect reloaded observation IDs
        reloaded_ids = {obs.obs_id for obs in scheduler2._queue}

        # IDs should match exactly
        assert original_ids == reloaded_ids
