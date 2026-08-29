"""Test that parse_failures table works correctly."""

import tempfile
from pathlib import Path

from mcbot.db import init_db, insert_parse_failure
from mcbot.timeutil import utcnow_iso


def test_insert_parse_failure_creates_row():
    """Test that insert_parse_failure writes to database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert a parse failure
        received_ts = utcnow_iso()
        raw_frame = '{"unexpected": "format", "not": "migration"}'
        reason = "unexpected_frame_shape"

        row_id = insert_parse_failure(db, received_ts, raw_frame, reason)

        assert row_id > 0

        # Verify it was inserted
        cursor = db.execute(
            "SELECT received_ts, raw_frame, reason, parser_version FROM parse_failures WHERE id = ?",
            (row_id,)
        )
        row = cursor.fetchone()

        assert row is not None
        assert row[0] == received_ts
        assert row[1] == raw_frame
        assert row[2] == reason
        assert row[3] == "1"  # default parser_version


def test_parse_failure_with_custom_parser_version():
    """Test parse failure with custom parser version."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        row_id = insert_parse_failure(
            db,
            received_ts=utcnow_iso(),
            raw_frame="bad frame",
            reason="test_reason",
            parser_version="2"
        )

        cursor = db.execute(
            "SELECT parser_version FROM parse_failures WHERE id = ?",
            (row_id,)
        )
        row = cursor.fetchone()

        assert row[0] == "2"


def test_parse_failure_timestamps_are_timezone_aware():
    """Test that parse_failures timestamps have timezone info."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        insert_parse_failure(
            db,
            received_ts=utcnow_iso(),
            raw_frame="test",
            reason="test"
        )

        cursor = db.execute(
            "SELECT received_ts, collected_at_utc FROM parse_failures"
        )
        row = cursor.fetchone()

        # Both timestamps should end with +00:00
        assert row[0].endswith("+00:00"), f"received_ts missing timezone: {row[0]}"
        assert row[1].endswith("+00:00"), f"collected_at_utc missing timezone: {row[1]}"
