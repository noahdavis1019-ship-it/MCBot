# Experiment Registry

## EXP-001: Migration Census with Forward Returns and Execution Costs

**Status**: RUNNING
**Started**: 2026-08-29
**Owner**: Research

### Question

What is the unconditional forward-return distribution for pump.fun tokens after Raydium migration, and what is the real executable round-trip cost?

### Hypothesis

The median round-trip execution cost (as measured by Jupiter quotes) is a significant fraction of short-horizon returns, and most strategies will need >X% edge to be profitable after costs. We cannot state X until we measure it.

### Method

1. **Universe**: All pump.fun tokens that emit a `migrationCreate` event on the PumpPortal websocket
2. **Horizons**: Measure price, liquidity, volume, FDV at t+1m, 5m, 15m, 30m, 1h, 4h, 24h after migration
3. **Source**: DexScreener API for price/liquidity/volume data
4. **Cost probe**: For a 25% random sample (seeded), get Jupiter quotes for:
   - 0.1 SOL → token at t+1m, 5m, 15m, 1h
   - Reciprocal token → SOL at same horizons
   - Slippage: 100 bps requested
   - Derive round-trip cost = (1 - (out_SOL / in_SOL)) as percentage
5. **Rate limits**:
   - DexScreener: ~60 req/min globally, token bucket enforced
   - Jupiter: 1 req/sec globally, token bucket enforced
6. **Data quality**: Log every missed/late observation explicitly, never fail silently

### Metrics

- **Primary**: Median round-trip execution cost (%) at t+1m, 5m, 15m, 1h
- **Secondary**: Forward return distribution at all horizons (percentiles: p10, p25, p50, p75, p90, p95, p99)
- **Tertiary**: Liquidity thresholds where quoted slippage becomes prohibitive

### Success Criteria

After 24h of unattended operation:
- ≥1 migration row per observed event
- ≥90% of scheduled observations either completed or explicitly logged as missed
- Zero silent gaps in data
- Zero duplicate websocket connections
- A `SELECT` query that returns forward return and quoted round-trip cost for a sampled mint

### Kill Criteria

- PumpPortal bans the connection (rate limit violation, terms violation)
- Jupiter bans the connection
- DexScreener rate limits become too restrictive to collect meaningful data
- Migration event rate drops to zero for >48h (market dead)

### Results

_Pending 24h collection cycle_

Expected deliverables:
- Median round-trip cost at each probe horizon
- Forward return distribution (full percentile table)
- Observation completion rate (actual vs. scheduled)
- Sample query demonstrating cost vs. return for a randomly selected mint

---

## Future Experiments

_None planned until EXP-001 completes_
