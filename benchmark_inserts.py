"""Benchmark SQLite insert latency for creation events.

Measures insert performance to estimate database growth during 24h collection.
"""

import sqlite3
import tempfile
import time
from pathlib import Path

from mcbot.db import init_db, insert_creation


def benchmark_creation_inserts(num_inserts: int = 1000) -> dict:
    """Benchmark creation insert latency.

    Args:
        num_inserts: Number of test inserts to perform

    Returns:
        Dictionary with latency statistics and DB growth estimate
    """
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "benchmark.db"
        db = init_db(db_path)

        # Get initial DB size
        initial_size_bytes = db_path.stat().st_size

        # Perform inserts and measure latency
        latencies = []

        for i in range(num_inserts):
            # Sample creation payload (typical size from observed data)
            start = time.perf_counter()

            insert_creation(
                conn=db,
                mint=f"BenchmarkMint{i:06d}1111111111111111111111111",
                signature=f"BenchmarkSig{i:06d}" + "x" * 70,
                recv_ts_utc="2026-08-29T12:00:00.000000+00:00",
                raw_payload='{"mint":"test","txType":"create","pool":"pump","symbol":"TEST","name":"Test Token",' +
                           '"bondingCurveKey":"xxx","traderPublicKey":"yyy","initialBuy":100,"solAmount":1000000,' +
                           '"marketCapSol":50000,"vSolInBondingCurve":30000,"vTokensInBondingCurve":800000000,' +
                           '"is_mayhem_mode":false,"uri":"https://example.com/metadata.json"}',
                name=f"Benchmark Token {i}",
                symbol=f"BENCH{i}",
                uri="https://example.com/metadata.json",
                bonding_curve_key=f"BondingCurve{i}",
                trader_public_key=f"TraderKey{i}",
                initial_buy=100000000,
                sol_amount=1000000,
                market_cap_sol=50000000000,
                v_sol_in_bonding_curve=30000000000,
                v_tokens_in_bonding_curve=800000000000,
                pool="pump",
                is_mayhem_mode=False,
            )

            end = time.perf_counter()
            latencies.append((end - start) * 1000)  # Convert to milliseconds

        # Commit all inserts
        db.commit()

        # Get final DB size
        final_size_bytes = db_path.stat().st_size
        db_growth_bytes = final_size_bytes - initial_size_bytes

        db.close()

    # Calculate statistics
    latencies.sort()
    n = len(latencies)

    def percentile(data, p):
        k = (n - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= n:
            return data[f]
        d0 = data[f] * (c - k)
        d1 = data[c] * (k - f)
        return d0 + d1

    return {
        "num_inserts": num_inserts,
        "latency_ms": {
            "mean": sum(latencies) / n,
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p99": percentile(latencies, 99),
            "max": max(latencies),
        },
        "db_growth": {
            "bytes_per_insert": db_growth_bytes / num_inserts,
            "mb_per_1k_inserts": (db_growth_bytes / num_inserts) * 1000 / (1024 ** 2),
            "estimated_mb_per_24h": (db_growth_bytes / num_inserts) * 25000 / (1024 ** 2),
        }
    }


if __name__ == "__main__":
    print("Benchmarking creation insert latency...")
    print()

    results = benchmark_creation_inserts(num_inserts=1000)

    print(f"Inserts performed: {results['num_inserts']:,}")
    print()

    print("Insert latency (ms):")
    print(f"  mean: {results['latency_ms']['mean']:.3f}")
    print(f"  p50:  {results['latency_ms']['p50']:.3f}")
    print(f"  p90:  {results['latency_ms']['p90']:.3f}")
    print(f"  p99:  {results['latency_ms']['p99']:.3f}")
    print(f"  max:  {results['latency_ms']['max']:.3f}")
    print()

    print("Database growth:")
    print(f"  Bytes per insert:      {results['db_growth']['bytes_per_insert']:.1f}")
    print(f"  MB per 1K inserts:     {results['db_growth']['mb_per_1k_inserts']:.2f}")
    print(f"  Estimated MB per 24h:  {results['db_growth']['estimated_mb_per_24h']:.2f}")
    print(f"  (assuming 25K creations/day)")
    print()

    # Throughput calculation
    mean_latency_s = results['latency_ms']['mean'] / 1000
    max_throughput = 1 / mean_latency_s if mean_latency_s > 0 else float('inf')
    print(f"Max throughput: {max_throughput:.0f} inserts/sec")
    print()

    # Expected creation rate: 25000/day = 1.04/min = 0.017/sec
    expected_rate = 25000 / (24 * 3600)
    print(f"Expected creation rate: {expected_rate:.3f} inserts/sec")
    print(f"Headroom: {max_throughput / expected_rate:.0f}x")
