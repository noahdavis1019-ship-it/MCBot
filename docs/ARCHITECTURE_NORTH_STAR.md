# ARCHITECTURE NORTH STAR

**Status**: Frozen reference (do not edit without explicit approval)
**Created**: 2026-08-29
**Purpose**: Immutable design constraints and principles for MCBot

---

## Core Principles

### 1. Research First, Trade Never
This is a data collection system for quant research. No trading logic exists or will be added without a new experiment specification. No wallet operations, no private keys, no transaction construction, no swap execution.

### 2. Forward Collection Only
Historical data carries lookahead bias. We collect forward from deployment time. Backfills are forbidden unless explicitly designed to preserve temporal integrity.

### 3. Append-Only Data
Database rows are never updated or deleted after insertion. Errors are logged as new rows. Schema drift is recorded in `parse_failures`. This preserves audit trails and prevents data corruption during analysis.

### 4. Explicit Gaps, No Silent Failures
When an observation cannot be collected (rate limit, HTTP error, parse failure), a row is written recording that fact with a status code and reason. Gaps in data must be visible in queries, never inferred from absence.

### 5. Single Process, Single Connection
- One Python process (no workers, no multiprocessing)
- One websocket connection to PumpPortal (multiple connections cause bans)
- Global rate limit enforcement via token buckets
- Graceful shutdown on SIGTERM/SIGINT

### 6. Free Tier Only
Jupiter 1 req/sec, DexScreener ~60 req/min, PumpPortal 1 connection (free subscriptions only for EXP-001). Budget constraints are enforced in code, not documentation.

### 7. Schema Versioning
Database schema has a version number. Migrations are explicit. Breaking changes require a new experiment and data dictionary update.

### 8. Test Against Reality
Test fixtures are recorded from production websockets, not invented. If the API changes format, tests fail loudly.

---

## Components

### Collector (collector.py)
- Maintains single persistent websocket to PumpPortal
- Exponential backoff reconnection (1s → 5min max)
- Subscribes to `subscribeNewToken` and `subscribeMigration` (both free)
- Never opens concurrent connections
- Records all frames verbatim; unparseable frames go to `parse_failures`

### Scheduler (scheduler.py)
- Priority queue for DexScreener observations at t+1m/5m/15m/30m/1h/4h/24h
- Respects global rate limiter (token bucket at ~60 rpm)
- Late observations are logged with actual vs scheduled timestamp
- Missed observations (rate limit exhausted) are written as rows with error status

### Quote Prober (probe.py)
- 25% sampling via seeded RNG (seed=42, deterministic)
- Bidirectional Jupiter quotes: SOL→TOKEN and TOKEN→SOL
- Horizons: t+1m/5m/15m/1h only (subset of observation horizons)
- Fixed parameters: 0.1 SOL, 100 bps slippage tolerance
- 1 req/sec global budget via token bucket

### Rate Limiter (ratelimit.py)
- Token bucket refill: DexScreener 1 tok/sec (cap 10), Jupiter 1 tok/sec (cap 5)
- Non-blocking `try_consume` and blocking `consume`
- Shared across all API calls to enforce global budget

### Database (db.py)
- SQLite with WAL mode for concurrent readers
- Four core tables: `migrations`, `observations`, `quote_probes`, `parse_failures`
- Schema version tracked in `schema_version` table
- All inserts append-only; no UPDATE or DELETE statements

---

## Data Flow

```
PumpPortal websocket
    ↓ (migration event)
insert_migration()
    ↓
schedule_observations() → priority queue
    ↓ (when scheduled time arrives)
try_acquire_dexscreener() → rate limiter
    ↓ (if token available)
HTTP GET dexscreener.com/latest/dex/tokens/{mint}
    ↓
insert_observation(price, liquidity, volume, ...)
```

```
migration event + RNG
    ↓ (if sampled, 25%)
maybe_schedule_probes() → priority queue
    ↓ (when scheduled time arrives)
try_acquire_jupiter() → rate limiter
    ↓ (if token available)
HTTP GET quote-api.jup.ag/v6/quote (SOL→TOKEN)
HTTP GET quote-api.jup.ag/v6/quote (TOKEN→SOL)
    ↓
insert_quote_probe(direction, in_amount, out_amount, ...)
```

---

## Error Handling

| Condition | Action |
|-----------|--------|
| Websocket disconnect | Reconnect with exponential backoff (never open second connection) |
| Unparseable websocket frame | Insert into `parse_failures`, log warning, continue |
| Rate limit exhausted | Defer observation (stays in queue), log delay |
| HTTP 4xx/5xx from API | Insert observation row with http_status=4xx/5xx, null data fields |
| Network timeout | Insert row with http_status=0, raw_payload=error string |

---

## Constraints

### Hard Limits
- **One** websocket connection (enforced by class design)
- **One** Python process (no subprocess spawning)
- DexScreener: ≤60 requests/minute (enforced by rate limiter)
- Jupiter: ≤1 request/second (enforced by rate limiter)
- Probe sampling: exactly 25% of migrations (seeded RNG at 42)

### Forbidden Operations
- Opening multiple websocket connections
- UPDATE or DELETE on data tables (schema_version excepted)
- Filtering/scoring tokens before recording
- Backfilling historical data
- Any transaction construction or wallet operations
- Adding dependencies beyond httpx/websockets/sqlite3/dotenv without justification

---

## Deployment

### Environment
```bash
# Required
PUMPPORTAL_API_KEY=<your-key>

# Optional (for future experiments)
# HELIUS_API_KEY=<key>
```

### Launch
```bash
python -m mcbot.collect
```

Process runs until SIGTERM/SIGINT. Graceful shutdown closes websocket, stops scheduler/prober, waits for async tasks to cancel.

### Monitoring
- Heartbeat row every 5 minutes (observation with http_status=NULL)
- Structured JSON logs to stdout (timestamp, level, message, extras)
- Check `parse_failures` for schema drift
- Query observation completion rate by horizon to detect rate limit saturation

---

## Evolution

This document is frozen. Changes require:
1. Explicit user approval
2. Update to version/date header
3. Justification in DECISIONS.md

Breaking this architecture (e.g., adding multiple connections, removing append-only constraint) requires a new experiment specification and explicit acknowledgment of trade-offs.
