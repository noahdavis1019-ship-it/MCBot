"""Tests for token creation tracking."""

import tempfile
import time
from pathlib import Path

from mcbot.db import init_db, insert_creation


def test_insert_creation():
    """Test creation insertion and retrieval."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert a creation
        creation_id = insert_creation(
            conn=db,
            mint="test_mint_123",
            signature="test_sig_456",
            recv_ts_utc="2026-08-29T12:00:00+00:00",
            raw_payload='{"test": "data"}',
            name="Test Token",
            symbol="TEST",
            trader_public_key="trader_wallet_789",
            market_cap_sol=10.5,
            pool="pump",
            is_mayhem_mode=True,
        )

        assert creation_id > 0

        # Retrieve and verify
        cursor = db.execute("SELECT mint, name, symbol, trader_public_key, market_cap_sol, is_mayhem_mode FROM creations WHERE id = ?", (creation_id,))
        row = cursor.fetchone()

        assert row[0] == "test_mint_123"
        assert row[1] == "Test Token"
        assert row[2] == "TEST"
        assert row[3] == "trader_wallet_789"
        assert row[4] == 10.5
        assert row[5] == 1  # True as integer


def test_insert_creation_unique_mint():
    """Test that mint is unique in creations table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert first creation
        insert_creation(
            conn=db,
            mint="duplicate_mint",
            signature="sig1",
            recv_ts_utc="2026-08-29T12:00:00+00:00",
            raw_payload='{}',
        )

        # Try to insert duplicate mint - should fail
        try:
            insert_creation(
                conn=db,
                mint="duplicate_mint",
                signature="sig2",
                recv_ts_utc="2026-08-29T12:01:00+00:00",
                raw_payload='{}',
            )
            assert False, "Expected UNIQUE constraint to fail"
        except Exception as e:
            assert "UNIQUE" in str(e)


def test_token_lifecycle_view():
    """Test token_lifecycle view joins creations to migrations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        from mcbot.db import insert_migration

        # Insert a creation
        insert_creation(
            conn=db,
            mint="test_mint",
            signature="sig1",
            recv_ts_utc="2026-08-29T12:00:00+00:00",
            raw_payload='{}',
            name="Test Token",
            symbol="TEST",
            trader_public_key="trader1",
        )

        # Insert corresponding migration
        insert_migration(
            conn=db,
            mint="test_mint",
            symbol="TEST",
            pool="raydium",
            migration_ts_utc="2026-08-29T12:05:00+00:00",
            raw_payload='{}',
        )

        # Query view
        cursor = db.execute("""
            SELECT mint, name, symbol, trader_public_key,
                   created_ts_utc, migrated_ts_utc, seconds_to_migration
            FROM token_lifecycle
            WHERE mint = 'test_mint'
        """)
        row = cursor.fetchone()

        assert row[0] == "test_mint"
        assert row[1] == "Test Token"
        assert row[2] == "TEST"
        assert row[3] == "trader1"
        assert row[4] == "2026-08-29T12:00:00+00:00"
        assert row[5] == "2026-08-29T12:05:00+00:00"
        assert abs(row[6] - 300) <= 1  # 5 minutes (±1s tolerance for rounding)


def test_token_lifecycle_view_no_migration():
    """Test token_lifecycle view for tokens that haven't migrated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert creation without migration
        insert_creation(
            conn=db,
            mint="test_mint_no_migrate",
            signature="sig1",
            recv_ts_utc="2026-08-29T12:00:00+00:00",
            raw_payload='{}',
            name="Unmigrated Token",
        )

        # Query view
        cursor = db.execute("""
            SELECT mint, name, migrated_ts_utc, seconds_to_migration
            FROM token_lifecycle
            WHERE mint = 'test_mint_no_migrate'
        """)
        row = cursor.fetchone()

        assert row[0] == "test_mint_no_migrate"
        assert row[1] == "Unmigrated Token"
        assert row[2] is None  # No migration
        assert row[3] is None  # No time to migration


def test_creation_volume_throughput():
    """Test insert throughput at ~25,000 rows/day volume (17-18 per minute).

    Expected daily volume: ~25,000 creates
    Per minute: ~17.4 creates
    Per 30 minutes: ~521 creates

    This test inserts 500 creations and measures latency.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        num_creates = 500
        start_time = time.time()

        for i in range(num_creates):
            insert_creation(
                conn=db,
                mint=f"mint_{i}",
                signature=f"sig_{i}",
                recv_ts_utc="2026-08-29T12:00:00+00:00",
                raw_payload='{}',
                name=f"Token {i}",
                symbol=f"TKN{i}",
                trader_public_key=f"trader_{i % 100}",  # 100 unique traders
            )

        end_time = time.time()
        elapsed = end_time - start_time
        avg_latency_ms = (elapsed / num_creates) * 1000

        # Verify all inserted
        cursor = db.execute("SELECT COUNT(*) FROM creations")
        count = cursor.fetchone()[0]
        assert count == num_creates

        # Report performance
        print(f"\n--- Creation Volume Test ---")
        print(f"Inserts: {num_creates}")
        print(f"Total time: {elapsed:.2f}s")
        print(f"Avg latency: {avg_latency_ms:.2f}ms per insert")
        print(f"Throughput: {num_creates / elapsed:.1f} inserts/sec")
        print(f"Expected daily volume (25K/day): {25000 * avg_latency_ms / 1000 / 60:.1f} minutes")

        # Assert reasonable performance (should be well under 1 second for 500 inserts)
        assert elapsed < 5.0, f"Too slow: {elapsed:.2f}s for {num_creates} inserts"
        assert avg_latency_ms < 10, f"Avg latency too high: {avg_latency_ms:.2f}ms"


def test_trader_index_exists():
    """Test that trader_public_key index exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        cursor = db.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_creations_trader'
        """)
        assert cursor.fetchone() is not None
