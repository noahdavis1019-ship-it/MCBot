"""Lint tests to enforce timezone-aware datetime usage."""

import re
from pathlib import Path


def test_no_naive_utcnow():
    """Ensure no code uses naive datetime.utcnow()."""
    mcbot_dir = Path(__file__).parent.parent / "mcbot"
    violations = []

    for py_file in mcbot_dir.glob("*.py"):
        if py_file.name == "timeutil.py":
            # timeutil.py is allowed to mention utcnow in comments/docstrings
            continue

        content = py_file.read_text()

        # Check for datetime.utcnow() calls (not in comments)
        for line_num, line in enumerate(content.splitlines(), 1):
            # Skip comments
            if line.strip().startswith("#"):
                continue

            if re.search(r'datetime\.utcnow\(\)', line):
                violations.append(f"{py_file.name}:{line_num}: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} naive datetime.utcnow() calls:\n" +
        "\n".join(violations) +
        "\n\nUse mcbot.timeutil.utcnow_iso() instead."
    )


def test_no_naive_fromtimestamp():
    """Ensure no code uses naive datetime.fromtimestamp()."""
    mcbot_dir = Path(__file__).parent.parent / "mcbot"
    violations = []

    for py_file in mcbot_dir.glob("*.py"):
        if py_file.name == "timeutil.py":
            # timeutil.py defines the safe version
            continue

        content = py_file.read_text()

        # Check for fromtimestamp without tz= parameter
        for line_num, line in enumerate(content.splitlines(), 1):
            # Skip comments
            if line.strip().startswith("#"):
                continue

            # Match fromtimestamp( but not fromtimestamp(..., tz=
            if re.search(r'fromtimestamp\([^,)]+\)(?!.*tz=)', line):
                violations.append(f"{py_file.name}:{line_num}: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} naive fromtimestamp() calls:\n" +
        "\n".join(violations) +
        "\n\nUse mcbot.timeutil.ts_to_utc_iso() instead."
    )


def test_all_db_timestamps_are_timezone_aware():
    """Verify all timestamp columns in database end with +00:00."""
    import tempfile
    from mcbot.db import init_db, insert_migration, insert_observation, insert_quote_probe

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = init_db(db_path)

        # Insert test data
        insert_migration(
            db,
            mint="test_mint",
            symbol="TEST",
            pool="pool123",
            migration_ts_utc="2026-01-01T00:00:00+00:00",
            raw_payload="{}"
        )

        insert_observation(
            db,
            mint="test_mint",
            horizon_label="1m",
            scheduled_ts_utc="2026-01-01T00:01:00+00:00",
            actual_ts_utc="2026-01-01T00:01:02+00:00",
        obs_status="OK",
            http_status=200,
        )

        insert_quote_probe(
            db,
            mint="test_mint",
            probe_ts_utc="2026-01-01T00:01:00+00:00",
            direction="SOL->TOKEN",
            in_amount_lamports=100_000_000,
            out_amount=1000,
            price_impact_pct=0.5,
            route_plan_json="[]",
            http_status=200,
            request_latency_ms=100,
            raw_payload="{}",
        )

        # Check all timestamp columns
        violations = []

        for table, ts_columns in [
            ("migrations", ["migration_ts_utc", "collected_at_utc"]),
            ("observations", ["scheduled_ts_utc", "actual_ts_utc", "collected_at_utc"]),
            ("quote_probes", ["probe_ts_utc", "collected_at_utc"]),
            ("parse_failures", ["received_ts", "collected_at_utc"]),
        ]:
            for col in ts_columns:
                cursor = db.execute(f"SELECT {col} FROM {table}")
                for row in cursor.fetchall():
                    ts_value = row[0]
                    if ts_value and not ts_value.endswith("+00:00"):
                        violations.append(f"{table}.{col}: {ts_value}")

        assert not violations, (
            f"Found {len(violations)} timestamps without +00:00:\n" +
            "\n".join(violations)
        )
