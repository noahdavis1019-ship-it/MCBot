# Data Dictionary

Schema version: 1

## Principles

- **Append-only**: No updates or deletes after row insertion
- **Explicit nulls**: NULL means "not applicable", not "missing" - missing data is logged with status codes
- **UTC timestamps**: All timestamps in UTC, stored as ISO 8601 strings
- **Raw payloads**: Every row stores the original API response for auditability

## Tables

### migrations

Records pump.fun token migration events from PumpPortal websocket.

| Column | Type | Nullable | Description | Units | Source |
|--------|------|----------|-------------|-------|--------|
| id | INTEGER PRIMARY KEY | NO | Auto-increment row ID | - | SQLite |
| mint | TEXT | NO | Solana token mint address | - | PumpPortal |
| symbol | TEXT | YES | Token symbol/ticker | - | PumpPortal |
| pool | TEXT | YES | Raydium pool address | - | PumpPortal |
| migration_ts_utc | TEXT | NO | Migration timestamp | ISO 8601 UTC | PumpPortal |
| source | TEXT | NO | Always "pumpportal" | - | System |
| raw_payload | TEXT | NO | Full JSON payload from websocket | - | PumpPortal |
| collected_at_utc | TEXT | NO | Row insertion timestamp | ISO 8601 UTC | System |

**Indexes**:
- `idx_migrations_mint` on `mint`
- `idx_migrations_ts` on `migration_ts_utc`

**Notes**:
- Duplicates are possible if websocket sends duplicate messages
- `symbol` and `pool` may be NULL if missing in payload
- `migration_ts_utc` comes from payload, `collected_at_utc` is system clock

**Multi-Launchpad Universe (Critical Finding)**:

Based on analysis of `tests/fixtures/pumpportal_raw.jsonl` (546 frames, mtime: 2026-08-29 06:48:42):
- **Creation pools observed**: `pump` (523), `bonk` (2)
- **Migration pools observed**: `pump-amm` (18)
- **Creation events**: 525 total
- **Migration events**: 18 total

This reveals that:
1. **The creation universe spans multiple launchpads** - not just pump.fun
2. **The migration universe is pump-amm only** - in this sample
3. **Creation pool ≠ migration pool** - these are distinct event types from different launchpad systems

**Implications for Analysis**:
- Do NOT compute aggregate migration/creation conversion rates without per-pool breakdowns
- **Open question**: Do `bonk` graduations appear in `subscribeMigration` at all, or only pump.fun graduations?
- The 24-hour report MUST include:
  - Creations by pool: `{pool: count}`
  - Migrations by pool: `{pool: count}`
  - Conversion rate per pool: `migrations[pool] / creations[pool]`
- Treating this as a homogeneous population would be a methodological error

### observations

Records price/liquidity/volume observations at scheduled horizons after migration.

| Column | Type | Nullable | Description | Units | Source |
|--------|------|----------|-------------|-------|--------|
| id | INTEGER PRIMARY KEY | NO | Auto-increment row ID | - | SQLite |
| mint | TEXT | NO | Token mint address | - | Derived from migration |
| horizon_label | TEXT | NO | Horizon name: 1m, 5m, 15m, 30m, 1h, 4h, 24h | - | System |
| scheduled_ts_utc | TEXT | NO | When observation was scheduled | ISO 8601 UTC | System |
| actual_ts_utc | TEXT | NO | When observation was attempted | ISO 8601 UTC | System |
| price_usd | REAL | YES | Token price in USD | USD | DexScreener |
| price_native | REAL | YES | Token price in SOL | SOL | DexScreener |
| liquidity_usd | REAL | YES | Pool liquidity | USD | DexScreener |
| fdv | REAL | YES | Fully diluted valuation | USD | DexScreener |
| vol_5m | REAL | YES | 5-minute volume | USD | DexScreener |
| vol_1h | REAL | YES | 1-hour volume | USD | DexScreener |
| txns_buys_5m | INTEGER | YES | Buy transactions (5m) | count | DexScreener |
| txns_sells_5m | INTEGER | YES | Sell transactions (5m) | count | DexScreener |
| dex_id | TEXT | YES | DEX identifier (e.g., "raydium") | - | DexScreener |
| http_status | INTEGER | YES | HTTP status code (NULL = heartbeat) | - | System |
| request_latency_ms | INTEGER | YES | Request round-trip time | milliseconds | System |
| raw_payload | TEXT | YES | Full JSON response from API | - | DexScreener |
| collected_at_utc | TEXT | NO | Row insertion timestamp | ISO 8601 UTC | System |

**Indexes**:
- `idx_observations_mint` on `mint`
- `idx_observations_horizon` on `horizon_label`
- `idx_observations_scheduled` on `scheduled_ts_utc`

**Notes**:
- Heartbeat rows: `http_status IS NULL`, inserted every 5 minutes to detect downtime
- Missed observations: `http_status` indicates failure (4xx/5xx), all data columns NULL
- Late observations: `actual_ts_utc - scheduled_ts_utc > threshold` due to rate limiting
- Forward returns: Calculate as `(price_usd - initial_price) / initial_price`
- Join to migrations on `mint` to get migration timestamp and calculate horizon deltas

### quote_probes

Records Jupiter quote API calls for execution cost estimation.

| Column | Type | Nullable | Description | Units | Source |
|--------|------|----------|-------------|-------|--------|
| id | INTEGER PRIMARY KEY | NO | Auto-increment row ID | - | SQLite |
| mint | TEXT | NO | Token mint address | - | Derived from migration |
| probe_ts_utc | TEXT | NO | When quote was requested | ISO 8601 UTC | System |
| direction | TEXT | NO | "SOL->TOKEN" or "TOKEN->SOL" | - | System |
| in_amount_lamports | INTEGER | NO | Input amount | lamports (1e-9 SOL) | System |
| out_amount | INTEGER | YES | Output amount quoted | lamports or token units | Jupiter |
| price_impact_pct | REAL | YES | Price impact percentage | percent | Jupiter |
| route_plan_json | TEXT | YES | Full route plan from Jupiter | JSON | Jupiter |
| slippage_bps_requested | INTEGER | NO | Slippage tolerance | basis points | System (always 100) |
| http_status | INTEGER | NO | HTTP status code | - | System |
| request_latency_ms | INTEGER | NO | Request round-trip time | milliseconds | System |
| raw_payload | TEXT | YES | Full JSON response | - | Jupiter |
| collected_at_utc | TEXT | NO | Row insertion timestamp | ISO 8601 UTC | System |

**Indexes**:
- `idx_quote_probes_mint` on `mint`
- `idx_quote_probes_ts` on `probe_ts_utc`
- `idx_quote_probes_direction` on `direction`

**Notes**:
- Sampled: 100% of migrations get probes (changed from 25% in schema v6)
- Bidirectional: For each sampled mint, both SOL→TOKEN and TOKEN→SOL are queried
- Horizons: Probes at t+1m, 5m, 15m, 1h after migration
- `in_amount_lamports`: Always 100000000 (0.1 SOL) for SOL→TOKEN direction
- Round-trip cost: For a pair (SOL→TOKEN, TOKEN→SOL), calculate `1 - (final_SOL / initial_SOL)`
- Failure: `http_status >= 400`, `out_amount` and related fields NULL
- Sample rate is recorded in `config` table with timestamp for historical tracking

## Derived Metrics

### Forward Returns

```sql
SELECT
  o.mint,
  o.horizon_label,
  o.price_usd,
  first.price_usd as initial_price,
  (o.price_usd - first.price_usd) / first.price_usd as forward_return_pct
FROM observations o
JOIN (
  SELECT mint, MIN(actual_ts_utc) as first_ts, price_usd
  FROM observations
  WHERE http_status = 200 AND price_usd IS NOT NULL
  GROUP BY mint
) first ON o.mint = first.mint
WHERE o.http_status = 200 AND o.price_usd IS NOT NULL;
```

### Round-Trip Cost

```sql
WITH pairs AS (
  SELECT
    mint,
    probe_ts_utc,
    MAX(CASE WHEN direction = 'SOL->TOKEN' THEN out_amount END) as tokens_received,
    MAX(CASE WHEN direction = 'TOKEN->SOL' THEN out_amount END) as sol_received
  FROM quote_probes
  WHERE http_status = 200
  GROUP BY mint, probe_ts_utc
)
SELECT
  mint,
  probe_ts_utc,
  (100000000.0 - sol_received) / 100000000.0 as round_trip_cost_pct
FROM pairs
WHERE tokens_received IS NOT NULL AND sol_received IS NOT NULL;
```

## Schema Versioning

| Version | Date | Changes |
|---------|------|---------|
| 1 | 2026-08-29 | Initial schema |

## Data Retention

No automatic deletion. Disk space permitting, retain all data indefinitely for historical analysis.

Consider manual purge of migrations older than 90 days if disk usage exceeds threshold.
