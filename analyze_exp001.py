"""EXP-001 Analysis: Trading Cost vs Forward Returns

Analyzes 24-hour collection to determine if rapid trading on migrated
tokens is viable after accounting for execution costs.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import json

DB_PATH = Path("data/mcbot.db")

def percentile(data, p):
    """Calculate percentile p (0-100) of data."""
    if not data:
        return None
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1

def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    print("=" * 80)
    print("EXP-001 ANALYSIS — TRADING COST VS FORWARD RETURNS")
    print("=" * 80)
    print()

    # ========================================================================
    # A — DATA INTEGRITY
    # ========================================================================
    print("A — DATA INTEGRITY")
    print("-" * 80)

    # Row counts per table
    tables = ['migrations', 'creations', 'observations', 'quote_probes',
              'connection_events', 'parse_failures', 'config']
    print("\nRow counts:")
    for table in tables:
        try:
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:20s}: {count:,}")
        except sqlite3.OperationalError:
            print(f"  {table:20s}: TABLE NOT FOUND")

    # Run window from connection_events
    print("\nRun window (from connection_events):")
    first_conn = db.execute(
        "SELECT MIN(ts_utc) FROM connection_events WHERE event = 'CONNECTED'"
    ).fetchone()[0]
    last_event = db.execute(
        "SELECT MAX(ts_utc) FROM connection_events"
    ).fetchone()[0]
    print(f"  First connection: {first_conn}")
    print(f"  Last event:       {last_event}")
    if first_conn and last_event:
        start = datetime.fromisoformat(first_conn)
        end = datetime.fromisoformat(last_event)
        duration = end - start
        print(f"  Duration:         {duration}")

    # Hourly coverage with connection events
    print("\nHourly coverage:")
    hourly = db.execute("""
        SELECT
            strftime('%Y-%m-%d %H:00', ts_utc) as hour,
            COUNT(CASE WHEN event = 'CONNECTED' THEN 1 END) as connects,
            COUNT(CASE WHEN event = 'DISCONNECTED' THEN 1 END) as disconnects
        FROM connection_events
        GROUP BY hour
        ORDER BY hour
    """).fetchall()

    for row in hourly:
        print(f"  {row['hour']}: {row['connects']} connects, {row['disconnects']} disconnects")

    # Observations by obs_status
    print("\nObservations by obs_status:")
    obs_status = db.execute("""
        SELECT obs_status, COUNT(*) as count
        FROM observations
        GROUP BY obs_status
        ORDER BY count DESC
    """).fetchall()
    for row in obs_status:
        print(f"  {row['obs_status']:20s}: {row['count']:,}")

    # Parse failures
    print("\nParse failures:")
    failures = db.execute("SELECT * FROM parse_failures ORDER BY received_ts").fetchall()
    print(f"  Total: {len(failures)}")
    if failures:
        print("\n  Full contents:")
        for f in failures:
            print(f"    {f['received_ts']}: {f['error_type']}")
            print(f"      {f['raw_frame'][:200]}...")

    # Creations analysis
    print("\nCreations:")
    total_creates = db.execute("SELECT COUNT(*) FROM creations").fetchone()[0]
    distinct_mints = db.execute("SELECT COUNT(DISTINCT mint) FROM creations").fetchone()[0]
    duplicates = total_creates - distinct_mints
    print(f"  Total:           {total_creates:,}")
    print(f"  Distinct mints:  {distinct_mints:,}")
    print(f"  Duplicates:      {duplicates:,}")

    # BY POOL breakdown
    print("\n  By pool:")
    pool_creates = db.execute("""
        SELECT pool, COUNT(*) as count
        FROM creations
        GROUP BY pool
        ORDER BY count DESC
    """).fetchall()
    for row in pool_creates:
        print(f"    {row['pool']:15s}: {row['count']:,} creations")

    pool_migrates = db.execute("""
        SELECT pool, COUNT(*) as count
        FROM migrations
        GROUP BY pool
        ORDER BY count DESC
    """).fetchall()
    print("\n  Migrations by pool:")
    for row in pool_migrates:
        print(f"    {row['pool']:15s}: {row['count']:,} migrations")

    # Conversion rates per pool
    print("\n  Conversion rate per pool:")
    pools_data = {}
    for row in pool_creates:
        pools_data[row['pool']] = {'creates': row['count'], 'migrates': 0}
    for row in pool_migrates:
        if row['pool'] in pools_data:
            pools_data[row['pool']]['migrates'] = row['count']
        else:
            pools_data[row['pool']] = {'creates': 0, 'migrates': row['count']}

    for pool, data in sorted(pools_data.items()):
        if data['creates'] > 0:
            rate = 100 * data['migrates'] / data['creates']
            print(f"    {pool:15s}: {data['migrates']:,} / {data['creates']:,} = {rate:.2f}%")
        else:
            print(f"    {pool:15s}: {data['migrates']:,} migrations, 0 creates")

    # t0_basis breakdown
    print("\nt0_basis breakdown (from token_lifecycle VIEW):")
    t0_basis = db.execute("""
        SELECT t0_basis, COUNT(*) as count
        FROM token_lifecycle
        WHERE migrated_ts_utc IS NOT NULL
        GROUP BY t0_basis
        ORDER BY count DESC
    """).fetchall()
    for row in t0_basis:
        print(f"  {row['t0_basis']:10s}: {row['count']:,}")

    # recv_minus_block timing
    print("\nrecv_minus_block_ms (block-anchored timing):")
    timing_data = db.execute("""
        SELECT
            (julianday(migration_ts_utc) - julianday(block_ts_utc)) * 86400000 as recv_minus_block_ms
        FROM migrations
        WHERE block_ts_utc IS NOT NULL
    """).fetchall()

    if timing_data:
        deltas = [row['recv_minus_block_ms'] for row in timing_data]
        print(f"  N = {len(deltas)}")
        print(f"  p10: {percentile(deltas, 10):.1f} ms")
        print(f"  p50: {percentile(deltas, 50):.1f} ms")
        print(f"  p90: {percentile(deltas, 90):.1f} ms")
    else:
        print("  No block-anchored migrations")

    # Helius credits consumed
    print("\nHelius credits consumed:")
    helius_calls = db.execute("""
        SELECT COUNT(*) FROM migrations WHERE block_ts_utc IS NOT NULL
    """).fetchone()[0]
    print(f"  getTransaction calls: {helius_calls:,}")
    print(f"  (1 credit per call)")

    print()

    # ========================================================================
    # B — COST FLOOR
    # ========================================================================
    print("B — COST FLOOR (Round-Trip Trading Cost)")
    print("-" * 80)

    # Get paired quotes
    paired_quotes = db.execute("""
        WITH sol_to_token AS (
            SELECT
                mint,
                probe_ts_utc,
                out_amount as tokens_received,
                http_status,
                price_impact_pct
            FROM quote_probes
            WHERE direction = 'SOL->TOKEN'
        ),
        token_to_sol AS (
            SELECT
                mint,
                probe_ts_utc,
                out_amount as sol_received,
                http_status
            FROM quote_probes
            WHERE direction = 'TOKEN->SOL'
        )
        SELECT
            s.mint,
            s.probe_ts_utc,
            s.tokens_received,
            t.sol_received,
            s.http_status as sol_to_token_status,
            t.http_status as token_to_sol_status,
            m.pool
        FROM sol_to_token s
        JOIN token_to_sol t
            ON s.mint = t.mint AND s.probe_ts_utc = t.probe_ts_utc
        LEFT JOIN migrations m ON s.mint = m.mint
    """).fetchall()

    print(f"\nProbe attempts: {len(paired_quotes):,} paired round-trips")

    # Count successes and failures
    successes = []
    no_route = 0
    http_failures = {}

    for row in paired_quotes:
        if row['sol_to_token_status'] == 200 and row['token_to_sol_status'] == 200:
            if row['tokens_received'] and row['sol_received']:
                # Calculate round-trip cost
                sol_spent = 100_000_000  # 0.1 SOL in lamports
                sol_returned = row['sol_received']
                cost_bps = 10_000 * (1 - (sol_returned / sol_spent))
                successes.append({
                    'mint': row['mint'],
                    'ts': row['probe_ts_utc'],
                    'cost_bps': cost_bps,
                    'pool': row['pool']
                })
            else:
                no_route += 1
        else:
            # Track HTTP failures
            for status in [row['sol_to_token_status'], row['token_to_sol_status']]:
                if status != 200:
                    http_failures[status] = http_failures.get(status, 0) + 1

    print(f"Successes: {len(successes):,}")
    print(f"No route (infinite cost): {no_route:,} ({100*no_route/len(paired_quotes):.1f}%)")
    print(f"HTTP failures by status:")
    for status, count in sorted(http_failures.items()):
        print(f"  {status}: {count:,}")

    if successes:
        costs = [s['cost_bps'] for s in successes]
        print(f"\nOverall round-trip cost (basis points):")
        print(f"  N = {len(costs):,}")
        print(f"  p10: {percentile(costs, 10):.1f}")
        print(f"  p25: {percentile(costs, 25):.1f}")
        print(f"  p50: {percentile(costs, 50):.1f}")
        print(f"  p75: {percentile(costs, 75):.1f}")
        print(f"  p90: {percentile(costs, 90):.1f}")

        # TODO: Bucket by liquidity, horizon, hour
        # This requires joining with observations for liquidity data
        # and parsing probe_ts_utc for horizon/hour

    print()

    # ========================================================================
    # C — UNCONDITIONAL FORWARD RETURNS
    # ========================================================================
    print("C — UNCONDITIONAL FORWARD RETURNS")
    print("-" * 80)

    # Get migrations with price observations
    horizons = ['1m', '5m', '15m', '30m', '1h', '4h', '24h']

    for horizon in horizons:
        # Get initial price (first successful observation)
        returns_data = db.execute(f"""
            WITH initial_prices AS (
                SELECT
                    mint,
                    price_usd as p0
                FROM observations
                WHERE horizon_label = '{horizon}'
                  AND obs_status = 'OK'
                  AND price_usd IS NOT NULL
                GROUP BY mint
                HAVING MIN(actual_ts_utc)
            ),
            horizon_prices AS (
                SELECT
                    mint,
                    price_usd as pH
                FROM observations
                WHERE horizon_label = '{horizon}'
                  AND obs_status = 'OK'
                  AND price_usd IS NOT NULL
            )
            SELECT
                i.mint,
                i.p0,
                h.pH,
                (h.pH / i.p0) - 1 as return_H
            FROM initial_prices i
            LEFT JOIN horizon_prices h ON i.mint = h.mint
        """).fetchall()

        # Count total migrations
        total_migs = db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0]

        returns = []
        abs_returns = []
        no_price = 0

        for row in returns_data:
            if row['pH'] is not None:
                ret = row['return_H']
                returns.append(ret)
                abs_returns.append(abs(ret))
            else:
                no_price += 1

        print(f"\n{horizon:5s}:")
        print(f"  N with price:     {len(returns):,}")
        print(f"  N without price:  {no_price:,} ({100*no_price/total_migs:.1f}% of migrations)")

        if returns:
            print(f"  return_H:")
            print(f"    mean:  {sum(returns)/len(returns):7.2%}")
            print(f"    p10:   {percentile(returns, 10):7.2%}")
            print(f"    p25:   {percentile(returns, 25):7.2%}")
            print(f"    p50:   {percentile(returns, 50):7.2%}")
            print(f"    p75:   {percentile(returns, 75):7.2%}")
            print(f"    p90:   {percentile(returns, 90):7.2%}")

            print(f"  |return_H|:")
            print(f"    mean:  {sum(abs_returns)/len(abs_returns):7.2%}")
            print(f"    p10:   {percentile(abs_returns, 10):7.2%}")
            print(f"    p25:   {percentile(abs_returns, 25):7.2%}")
            print(f"    p50:   {percentile(abs_returns, 50):7.2%}")
            print(f"    p75:   {percentile(abs_returns, 75):7.2%}")
            print(f"    p90:   {percentile(abs_returns, 90):7.2%}")

    print()

    # ========================================================================
    # D — VERDICT TABLE
    # ========================================================================
    print("D — VERDICT TABLE")
    print("-" * 80)
    print()
    print("Pre-registered decision rule:")
    print("  cost > p75(|ret|)  -> DEAD")
    print("  cost > p50(|ret|)  -> MARGINAL")
    print("  cost < p50(|ret|)  -> LIVE")
    print()

    if successes:
        median_cost_bps = percentile(costs, 50)

        print(f"Horizon | median_cost_bps | p50(|ret|) | p75(|ret|) | VERDICT")
        print("-" * 70)

        for horizon in horizons:
            # Recalculate returns for this horizon
            returns_data = db.execute(f"""
                WITH initial_prices AS (
                    SELECT
                        mint,
                        price_usd as p0
                    FROM observations
                    WHERE horizon_label = '{horizon}'
                      AND obs_status = 'OK'
                      AND price_usd IS NOT NULL
                    GROUP BY mint
                    HAVING MIN(actual_ts_utc)
                ),
                horizon_prices AS (
                    SELECT
                        mint,
                        price_usd as pH
                    FROM observations
                    WHERE horizon_label = '{horizon}'
                      AND obs_status = 'OK'
                      AND price_usd IS NOT NULL
                )
                SELECT
                    (h.pH / i.p0) - 1 as return_H
                FROM initial_prices i
                LEFT JOIN horizon_prices h ON i.mint = h.mint
                WHERE h.pH IS NOT NULL
            """).fetchall()

            if returns_data:
                abs_rets = [abs(row['return_H']) for row in returns_data]
                p50_abs_ret = percentile(abs_rets, 50) * 10000  # Convert to bps
                p75_abs_ret = percentile(abs_rets, 75) * 10000  # Convert to bps

                # Apply decision rule
                if median_cost_bps > p75_abs_ret:
                    verdict = "DEAD"
                elif median_cost_bps > p50_abs_ret:
                    verdict = "MARGINAL"
                else:
                    verdict = "LIVE"

                print(f"{horizon:7s} | {median_cost_bps:15.1f} | {p50_abs_ret:10.1f} | {p75_abs_ret:10.1f} | {verdict}")
            else:
                print(f"{horizon:7s} | {median_cost_bps:15.1f} | {'NO DATA':>10s} | {'NO DATA':>10s} | N/A")

    print()

    # ========================================================================
    # CAVEATS
    # ========================================================================
    print("CAVEATS")
    print("-" * 80)
    print("""
- Quotes exclude priority fees, failed transactions, quote-to-land
  latency, and MEV. Measured cost is a LOWER BOUND on real cost.

- |return| is an UPPER BOUND on capturable return (perfect direction).

- Therefore DEAD verdicts are conclusive; LIVE verdicts are provisional
  and mean only "not yet falsified."

- One day is one regime. Nothing here generalizes across regimes yet.
    """)

    db.close()

if __name__ == "__main__":
    main()
