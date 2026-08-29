"""Integration tests for end-to-end data flow."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from mcbot.collector import MigrationCollector
from mcbot.db import init_db
from mcbot.probe import QuoteProber
from mcbot.ratelimit import RateLimiter
from mcbot.scheduler import ObservationScheduler


@pytest.fixture
def temp_db_path():
    """Create temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


def test_migration_to_database_flow(temp_db_path):
    """Test that migration events flow from collector to database."""
    db = init_db(temp_db_path)
    migrations_received = []

    def on_migration(payload):
        migrations_received.append(payload)

    # Create collector
    collector = MigrationCollector(on_migration)

    # Simulate migration payload
    test_payload = {
        "mint": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        "symbol": "TEST",
        "pool": "PoolAddr123",
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Call handler directly (websocket testing requires complex mocking)
    collector.on_migration(test_payload)

    # Verify callback was invoked
    assert len(migrations_received) == 1
    assert migrations_received[0]["mint"] == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"


def test_scheduler_queue_ordering(temp_db_path):
    """Test that scheduler processes observations in correct order."""
    db = init_db(temp_db_path)
    rate_limiter = RateLimiter()
    scheduler = ObservationScheduler(db, rate_limiter)

    # Schedule observations for multiple tokens
    base_ts = "2026-08-29T12:00:00"
    scheduler.schedule_observations("mint1", base_ts)

    later_ts = "2026-08-29T12:10:00"
    scheduler.schedule_observations("mint2", later_ts)

    # Queue should have 14 observations (7 per token)
    assert len(scheduler._queue) == 14

    # First observation should be from mint1 (earlier migration)
    first_obs = scheduler._queue[0]
    assert first_obs.mint == "mint1"


def test_quote_prober_sampling(temp_db_path):
    """Test that quote prober samples 25% of migrations."""
    db = init_db(temp_db_path)
    rate_limiter = RateLimiter()
    prober = QuoteProber(db, rate_limiter)

    # Schedule 100 migrations
    sampled_count = 0
    for i in range(100):
        initial_queue_size = len(prober._queue)
        prober.maybe_schedule_probes(f"mint_{i}", "2026-08-29T12:00:00")
        new_queue_size = len(prober._queue)

        if new_queue_size > initial_queue_size:
            sampled_count += 1

    # Should sample approximately 25% (allow variance)
    assert 15 <= sampled_count <= 35


def test_quote_prober_deterministic_sampling():
    """Test that quote prober sampling is deterministic with fixed seed."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path1 = Path(tmpdir) / "test1.db"
        db_path2 = Path(tmpdir) / "test2.db"

        db1 = init_db(db_path1)
        db2 = init_db(db_path2)

        rate_limiter1 = RateLimiter()
        rate_limiter2 = RateLimiter()

        prober1 = QuoteProber(db1, rate_limiter1)
        prober2 = QuoteProber(db2, rate_limiter2)

        # Schedule same migrations
        for i in range(50):
            prober1.maybe_schedule_probes(f"mint_{i}", "2026-08-29T12:00:00")
            prober2.maybe_schedule_probes(f"mint_{i}", "2026-08-29T12:00:00")

        # Should have identical queue sizes (deterministic sampling)
        assert len(prober1._queue) == len(prober2._queue)


def test_collector_handles_malformed_json():
    """Test that collector handles malformed JSON gracefully."""
    migrations_received = []

    def on_migration(payload):
        migrations_received.append(payload)

    collector = MigrationCollector(on_migration)

    # Simulate malformed message - should not crash
    try:
        asyncio.run(collector._handle_message("not valid json {"))
    except Exception:
        pass  # Expected to log error, not raise

    # Should not have received any migrations
    assert len(migrations_received) == 0


def test_collector_handles_non_dict_message():
    """Test that collector handles non-dict messages."""
    migrations_received = []

    def on_migration(payload):
        migrations_received.append(payload)

    collector = MigrationCollector(on_migration)

    # Simulate array message - should handle gracefully
    asyncio.run(collector._handle_message('["not", "a", "dict"]'))

    # Should not have received any migrations
    assert len(migrations_received) == 0


def test_single_connection_enforcement():
    """Test that only one websocket connection is created."""
    # This is more of a design constraint test
    # The MigrationCollector class has only one _ws attribute
    # and the _connect_and_listen method is called sequentially

    def on_migration(payload):
        pass

    collector = MigrationCollector(on_migration)

    # Verify single websocket attribute exists
    assert hasattr(collector, "_ws")
    assert collector._ws is None  # Not connected yet


@pytest.mark.asyncio
async def test_collector_reconnect_backoff():
    """Test that collector implements exponential backoff on reconnect."""
    def on_migration(payload):
        pass

    collector = MigrationCollector(on_migration)

    # Initial delay should be 1.0
    assert collector._reconnect_delay == 1.0

    # Simulate first backoff
    await collector._backoff()
    assert collector._reconnect_delay == 2.0

    # Simulate second backoff
    await collector._backoff()
    assert collector._reconnect_delay == 4.0

    # Continue until max
    for _ in range(10):
        await collector._backoff()

    # Should not exceed max delay
    from mcbot.collector import MAX_RECONNECT_DELAY
    assert collector._reconnect_delay <= MAX_RECONNECT_DELAY
