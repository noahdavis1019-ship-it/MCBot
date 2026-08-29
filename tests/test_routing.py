"""Tests for three-way message routing in collector."""

import asyncio
import tempfile
from pathlib import Path

from mcbot.collector import MigrationCollector
from mcbot.db import init_db


def test_route_migration():
    """Test ROUTE 1: Migration frames route to on_migration callback."""
    # Real migration frame from fixture (line 529)
    migration_frame = '{"txType":"migrate","signature":"2Q1234...","mint":"Abc123pump","pool":"raydium"}'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    collector = MigrationCollector(on_migration)

    # Process the frame
    asyncio.run(collector._handle_message(migration_frame))

    # Assert migration was routed to callback
    assert len(migrations_received) == 1
    assert migrations_received[0]["mint"] == "Abc123pump"
    assert migrations_received[0]["pool"] == "raydium"
    assert "migration_ts_utc" in migrations_received[0]

    # Assert no ignored frames
    assert collector.get_and_reset_ignored_count() == 0


def test_route_token_create():
    """Test ROUTE 2: Token create frames increment ignored counter AND insert to DB."""
    # Real create frame from fixture
    create_frame = '{"txType":"create","signature":"xyz","mint":"test_mint","name":"Test Token","symbol":"TEST","traderPublicKey":"trader123"}'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        collector = MigrationCollector(on_migration, db)

        # Process the frame
        asyncio.run(collector._handle_message(create_frame))

        # Assert not routed to migration callback
        assert len(migrations_received) == 0

        # Assert counted as ignored (keeps counter for heartbeat monitoring)
        assert collector.get_and_reset_ignored_count() == 1

        # Assert creation row was inserted
        cursor = db.execute("SELECT COUNT(*) FROM creations")
        count = cursor.fetchone()[0]
        assert count == 1

        # Verify the creation data
        cursor = db.execute("SELECT mint, name, symbol, trader_public_key FROM creations")
        row = cursor.fetchone()
        assert row[0] == "test_mint"
        assert row[1] == "Test Token"
        assert row[2] == "TEST"
        assert row[3] == "trader123"


def test_route_subscription_message():
    """Test ROUTE 2: Subscription confirmation messages increment ignored counter."""
    # Real subscription message from fixture (line 2)
    sub_frame = '{"message":"Successfully subscribed to token creation events."}'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    collector = MigrationCollector(on_migration)

    # Process the frame
    asyncio.run(collector._handle_message(sub_frame))

    # Assert not routed to migration callback
    assert len(migrations_received) == 0

    # Assert counted as ignored
    assert collector.get_and_reset_ignored_count() == 1


def test_route_error_message():
    """Test ROUTE 2: Error/warning messages increment ignored counter."""
    # Real error frame from fixture (line 1)
    error_frame = '{"errors":"Invalid API key. PumpSwap data will not be streamed."}'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    collector = MigrationCollector(on_migration)

    # Process the frame
    asyncio.run(collector._handle_message(error_frame))

    # Assert not routed to migration callback
    assert len(migrations_received) == 0

    # Assert counted as ignored
    assert collector.get_and_reset_ignored_count() == 1


def test_route_unknown_frame():
    """Test ROUTE 3: Unknown frames insert parse_failure row."""
    # Synthetic unknown frame
    unknown_frame = '{"unknown_field":"value","foo":"bar"}'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        collector = MigrationCollector(on_migration, db)

        # Process the frame
        asyncio.run(collector._handle_message(unknown_frame))

        # Assert not routed to migration callback
        assert len(migrations_received) == 0

        # Assert not counted as ignored
        assert collector.get_and_reset_ignored_count() == 0

        # Assert parse_failure row was inserted
        cursor = db.execute("SELECT COUNT(*) FROM parse_failures")
        count = cursor.fetchone()[0]
        assert count == 1

        # Verify the failure reason
        cursor = db.execute("SELECT reason, raw_frame FROM parse_failures")
        row = cursor.fetchone()
        assert "Unknown frame structure" in row[0]
        assert row[1] == unknown_frame


def test_route_invalid_json():
    """Test ROUTE 3: Invalid JSON inserts parse_failure row."""
    invalid_json = 'not valid json at all'

    migrations_received = []

    def on_migration(payload: dict) -> None:
        migrations_received.append(payload)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        collector = MigrationCollector(on_migration, db)

        # Process the frame
        asyncio.run(collector._handle_message(invalid_json))

        # Assert parse_failure row was inserted
        cursor = db.execute("SELECT COUNT(*) FROM parse_failures")
        count = cursor.fetchone()[0]
        assert count == 1

        # Verify the failure reason
        cursor = db.execute("SELECT reason FROM parse_failures")
        reason = cursor.fetchone()[0]
        assert "JSON decode error" in reason


def test_ignored_counter_resets():
    """Test that get_and_reset_ignored_count() resets the counter."""
    create_frame = '{"txType":"create","mint":"test"}'

    def on_migration(payload: dict) -> None:
        pass

    collector = MigrationCollector(on_migration)

    # Process 3 create frames
    for _ in range(3):
        asyncio.run(collector._handle_message(create_frame))

    # Get count (should be 3)
    count1 = collector.get_and_reset_ignored_count()
    assert count1 == 3

    # Get count again (should be 0 - reset)
    count2 = collector.get_and_reset_ignored_count()
    assert count2 == 0
