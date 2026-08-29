"""Tests for token creation tracking."""

import json
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


def test_insert_creation_duplicate_mint():
    """Test that duplicate mints are stored and flagged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert first creation
        id1 = insert_creation(
            conn=db,
            mint="duplicate_mint",
            signature="sig1",
            recv_ts_utc="2026-08-29T12:00:00+00:00",
            raw_payload='{}',
        )

        # Insert duplicate mint - should succeed and be flagged
        id2 = insert_creation(
            conn=db,
            mint="duplicate_mint",
            signature="sig2",
            recv_ts_utc="2026-08-29T12:01:00+00:00",
            raw_payload='{}',
        )

        # Both inserts should succeed
        assert id1 > 0
        assert id2 > 0
        assert id2 != id1

        # Verify first frame is NOT flagged
        cursor = db.execute("SELECT duplicate_of_mint_seen FROM creations WHERE id = ?", (id1,))
        assert cursor.fetchone()[0] == 0

        # Verify second frame IS flagged
        cursor = db.execute("SELECT duplicate_of_mint_seen FROM creations WHERE id = ?", (id2,))
        assert cursor.fetchone()[0] == 1

        # Verify both rows exist
        cursor = db.execute("SELECT COUNT(*) FROM creations WHERE mint = 'duplicate_mint'")
        assert cursor.fetchone()[0] == 2


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


def test_fixture_replay_duplicates():
    """Replay full fixture to verify duplicate detection: 525 rows, 523 distinct mints, 2 flagged."""
    fixture_path = Path(__file__).parent / "fixtures" / "pumpportal_raw.jsonl"

    if not fixture_path.exists():
        # Skip if fixture not present (it's recorded separately)
        print(f"Skipping: fixture not found at {fixture_path}")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Parse and insert all create frames from fixture
        create_count = 0
        with open(fixture_path) as f:
            for line in f:
                # Fixture format: {"received_ts": "...", "frame": "{...}"}
                record = json.loads(line.strip())
                frame = json.loads(record["frame"])

                if frame.get("txType") == "create":
                    insert_creation(
                        conn=db,
                        mint=frame.get("mint", ""),
                        signature=frame.get("signature", ""),
                        recv_ts_utc=record.get("received_ts", ""),
                        raw_payload=record["frame"],  # Store the inner frame JSON string
                        name=frame.get("name"),
                        symbol=frame.get("symbol"),
                        uri=frame.get("uri"),
                        bonding_curve_key=frame.get("bondingCurveKey"),
                        trader_public_key=frame.get("traderPublicKey"),
                        initial_buy=frame.get("initialBuy"),
                        sol_amount=frame.get("solAmount"),
                        market_cap_sol=frame.get("marketCapSol"),
                        v_sol_in_bonding_curve=frame.get("vSolInBondingCurve"),
                        v_tokens_in_bonding_curve=frame.get("vTokensInBondingCurve"),
                        pool=frame.get("pool"),
                        is_mayhem_mode=frame.get("is_mayhem_mode"),
                    )
                    create_count += 1

        # Verify expected counts
        cursor = db.execute("SELECT COUNT(*) FROM creations")
        total_rows = cursor.fetchone()[0]

        cursor = db.execute("SELECT COUNT(DISTINCT mint) FROM creations")
        distinct_mints = cursor.fetchone()[0]

        cursor = db.execute("SELECT COUNT(*) FROM creations WHERE duplicate_of_mint_seen = 1")
        flagged_count = cursor.fetchone()[0]

        # Assert fixture reality: 525 create frames, 523 distinct mints, 2 duplicates
        assert total_rows == 525, f"Expected 525 rows, got {total_rows}"
        assert distinct_mints == 523, f"Expected 523 distinct mints, got {distinct_mints}"
        assert flagged_count == 2, f"Expected 2 flagged duplicates, got {flagged_count}"
