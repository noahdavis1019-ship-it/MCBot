"""Generate coverage reports for EXP-001 data collection."""

import argparse
import sys
from pathlib import Path

from mcbot.db import get_coverage_report, init_db


def print_coverage_table(rows: list[dict]) -> None:
    """Print coverage report as ASCII table.

    Args:
        rows: Coverage data from get_coverage_report()
    """
    if not rows:
        print("No data in reporting period")
        return

    # Print header
    print("=" * 110)
    print("HOURLY COVERAGE REPORT")
    print("=" * 110)
    print(f"{'Hour (UTC)':<20} {'HB Exp':<8} {'HB Rcv':<8} {'Uptime':<8} {'Migs':<6} {'OK':<8} {'Missed':<8} {'Error':<8}")
    print("-" * 110)

    # Print rows
    for row in rows:
        print(
            f"{row['hour_utc']:<20} "
            f"{row['heartbeats_expected']:<8} "
            f"{row['heartbeats_received']:<8} "
            f"{row['uptime_pct']:>6.1f}%  "
            f"{row['migrations']:<6} "
            f"{row['obs_ok']:<8} "
            f"{row['obs_missed']:<8} "
            f"{row['obs_error']:<8}"
        )

    print("=" * 110)

    # Print summary
    total_hours = len(rows)
    total_hb_expected = sum(r['heartbeats_expected'] for r in rows)
    total_hb_received = sum(r['heartbeats_received'] for r in rows)
    avg_uptime = (total_hb_received / total_hb_expected * 100) if total_hb_expected > 0 else 0
    total_migrations = sum(r['migrations'] for r in rows)
    total_ok = sum(r['obs_ok'] for r in rows)
    total_missed = sum(r['obs_missed'] for r in rows)
    total_error = sum(r['obs_error'] for r in rows)

    print(f"\nSummary:")
    print(f"  Hours covered:       {total_hours}")
    print(f"  Avg uptime:          {avg_uptime:.1f}%")
    print(f"  Total migrations:    {total_migrations}")
    print(f"  Total observations:  {total_ok + total_missed + total_error}")
    print(f"    OK:                {total_ok}")
    print(f"    MISSED_LATE:       {total_missed}")
    print(f"    HTTP_ERROR:        {total_error}")
    print()


def main() -> None:
    """Generate and print coverage report."""
    parser = argparse.ArgumentParser(
        description="Generate hourly coverage report for EXP-001 data collection"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".mcbot" / "data.db",
        help="Path to database (default: ~/.mcbot/data.db)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of hours to report (default: 24)",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start timestamp (ISO 8601 UTC), overrides --hours",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End timestamp (ISO 8601 UTC), defaults to now",
    )

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    # Connect to database
    conn = init_db(args.db)

    # Determine time range
    if args.start:
        start_ts = args.start
    else:
        from datetime import datetime, timedelta, timezone
        start_ts = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    end_ts = args.end

    # Get coverage data
    rows = get_coverage_report(conn, start_ts, end_ts)

    # Print report
    print_coverage_table(rows)


if __name__ == "__main__":
    main()
