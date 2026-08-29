"""Unit tests for observation scheduler."""

from datetime import datetime, timedelta, timezone

import pytest

from mcbot.scheduler import HORIZONS, ScheduledObservation


def test_horizons_defined():
    """Test that all required horizons are defined."""
    labels = [h[0] for h in HORIZONS]
    assert "1m" in labels
    assert "5m" in labels
    assert "15m" in labels
    assert "30m" in labels
    assert "1h" in labels
    assert "4h" in labels
    assert "24h" in labels


def test_horizon_offsets_correct():
    """Test that horizon offsets are correct in minutes."""
    expected = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "24h": 1440,
    }

    for label, minutes in HORIZONS:
        assert expected[label] == minutes


def test_scheduled_observation_ordering():
    """Test that ScheduledObservation orders by timestamp."""
    obs1 = ScheduledObservation(
        scheduled_ts=100.0,
        mint="mint1",
        horizon_label="1m",
        migration_ts="2026-08-29T12:00:00"
    )
    obs2 = ScheduledObservation(
        scheduled_ts=200.0,
        mint="mint2",
        horizon_label="5m",
        migration_ts="2026-08-29T12:00:00"
    )

    assert obs1 < obs2
    assert not (obs2 < obs1)


def test_schedule_observations_creates_all_horizons():
    """Test that scheduler creates observations for all horizons."""
    import tempfile
    from pathlib import Path

    from mcbot.db import init_db
    from mcbot.ratelimit import RateLimiter
    from mcbot.scheduler import ObservationScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule observations
        migration_ts = "2026-08-29T12:00:00"
        scheduler.schedule_observations("test_mint", migration_ts)

        # Should have 7 observations scheduled
        assert len(scheduler._queue) == 7

        # Check that all horizons are present
        labels = [obs.horizon_label for obs in scheduler._queue]
        assert set(labels) == {"1m", "5m", "15m", "30m", "1h", "4h", "24h"}


def test_schedule_observations_correct_timing():
    """Test that observations are scheduled at correct times."""
    import tempfile
    from pathlib import Path

    from mcbot.db import init_db
    from mcbot.ratelimit import RateLimiter
    from mcbot.scheduler import ObservationScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Schedule observations from a known timestamp
        migration_ts = "2026-08-29T12:00:00+00:00"
        base_time = datetime.fromisoformat(migration_ts)

        scheduler.schedule_observations("test_mint", migration_ts)

        # Check each observation's scheduled time
        for obs in scheduler._queue:
            if obs.horizon_label == "1m":
                expected_ts = (base_time + timedelta(minutes=1)).timestamp()
                assert abs(obs.scheduled_ts - expected_ts) < 1.0
            elif obs.horizon_label == "5m":
                expected_ts = (base_time + timedelta(minutes=5)).timestamp()
                assert abs(obs.scheduled_ts - expected_ts) < 1.0
            elif obs.horizon_label == "24h":
                expected_ts = (base_time + timedelta(hours=24)).timestamp()
                assert abs(obs.scheduled_ts - expected_ts) < 1.0


def test_schedule_handles_utc_timezone():
    """Test that scheduler correctly handles UTC timezone indicators."""
    import tempfile
    from pathlib import Path

    from mcbot.db import init_db
    from mcbot.ratelimit import RateLimiter
    from mcbot.scheduler import ObservationScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)
        rate_limiter = RateLimiter()

        scheduler = ObservationScheduler(db, rate_limiter)

        # Test with different UTC formats
        for migration_ts in [
            "2026-08-29T12:00:00Z",
            "2026-08-29T12:00:00+00:00",
            "2026-08-29T12:00:00",
        ]:
            scheduler._queue = []  # Clear queue
            scheduler.schedule_observations("test_mint", migration_ts)

            # Should create 7 observations regardless of format
            assert len(scheduler._queue) == 7
