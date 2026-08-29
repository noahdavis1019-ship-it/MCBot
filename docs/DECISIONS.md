# Design Decisions

## Why migration-based universe?

**Decision**: Track only tokens that have completed Raydium migration via pump.fun, not pre-migration tokens.

**Rationale**:
- Pre-migration tokens have no DEX liquidity, making execution cost measurement impossible
- Migration is a natural filter indicating the token achieved bonding curve graduation
- DexScreener and Jupiter only work with migrated (DEX-listed) tokens
- Reduces noise from abandoned/failed launches
- Bias: This excludes tokens that never migrate, which may be the majority - but those are not tradeable via DEX

**Consequences**:
- Survival bias: We only see tokens that "succeeded" enough to migrate
- Cannot measure pre-migration dynamics or graduation probability
- Universe is smaller but higher quality for execution cost research

---

## Why these specific horizons (1m, 5m, 15m, 30m, 1h, 4h, 24h)?

**Decision**: Seven observation horizons ranging from 1 minute to 24 hours.

**Rationale**:
- **1m, 5m, 15m**: Capture immediate post-migration pump/dump dynamics
- **30m, 1h**: Medium-term price discovery
- **4h**: Intraday reversion behavior
- **24h**: Daily performance, commonly reported metric
- Trade-off: More horizons = more data but higher API costs and rate limit pressure

**Alternatives considered**:
- Fewer horizons (5m, 1h, 24h): Would reduce API load but miss granular dynamics
- More horizons (every minute): Rate limits make this infeasible
- Longer horizons (7d, 30d): Interesting but most tokens die fast - cost/benefit poor

**Consequences**:
- ~7 DexScreener requests per migration event over 24h
- Rate limit budget must support burst of migrations followed by scheduled observations

---

## Why 0.1 SOL probe size?

**Decision**: Use 0.1 SOL (100M lamports) for Jupiter quote probes.

**Rationale**:
- **Realistic trade size**: Small enough to be affordable for retail, large enough to be meaningful
- **Slippage sensitivity**: Large enough to trigger measurable slippage in low-liquidity pools
- **API limits**: Jupiter has no minimum, but too-small amounts may hit token precision floors
- **Cost basis**: Execution cost as percentage is more interpretable with a fixed SOL amount

**Alternatives considered**:
- 0.01 SOL: Too small, slippage may be artificially low and not representative
- 1.0 SOL: Would show higher slippage but unrealistic for most traders, may also hit route failures
- Dynamic sizing based on liquidity: Adds complexity, harder to compare across tokens

**Consequences**:
- Results are specific to ~0.1 SOL order size
- Slippage will be non-linear with size - do not extrapolate to 1 SOL or 10 SOL without further probes

---

## Why 100 bps slippage tolerance?

**Decision**: Request 1% (100 bps) slippage tolerance on Jupiter quotes.

**Rationale**:
- **Default**: Jupiter's default is 50 bps, but memecoin volatility often exceeds this
- **Realism**: 1% is a common setting for volatile/low-liquidity tokens
- **Failure rate**: Too tight (e.g., 10 bps) would result in frequent quote failures
- **Not a target**: This is max slippage tolerance, not expected slippage - actual slippage comes from quote

**Consequences**:
- Quoted routes may use up to 1% slippage
- Actual execution could differ (we don't execute, only quote)

---

## Why 25% sampling for quote probes?

**Decision**: Probe only 25% of migrations, selected via seeded RNG.

**Rationale**:
- **Rate limits**: Jupiter is 1 req/sec = 60/min, but DexScreener is also ~60/min
- **Burst capacity**: If 10 migrations arrive in 1 minute, that's 10×7 = 70 DexScreener requests scheduled over 24h
- Adding quote probes (4 horizons × 2 directions = 8 per migration) would quickly exhaust budgets
- **25% sample**: Provides statistical power while staying within limits
- **Seeded RNG**: Ensures reproducibility and prevents selection bias from time-of-day patterns

**Alternatives considered**:
- 100% sampling: Would hit rate limits immediately during migration bursts
- 10% sampling: More conservative but may reduce statistical power for rare edge cases
- Adaptive sampling based on rate limit headroom: More complex, harder to reason about bias

**Consequences**:
- 75% of migrations have no cost probes
- Statistical analysis must account for sampling
- Seed is recorded in code, changing it invalidates cross-experiment comparisons

---

## Why single-process architecture?

**Decision**: Single Python process, no multiprocessing, no async task queue (Celery/RQ), no separate workers.

**Rationale**:
- **Simplicity**: Easier to reason about, debug, and monitor
- **Rate limits**: Global rate limits require coordination - single process makes this trivial
- **Failure modes**: Fewer moving parts, no worker restart logic, no queue persistence
- **Cost**: Free-tier constraints mean throughput is limited by API quotas, not CPU
- **Websocket**: PumpPortal allows only one connection - multiple workers would require fan-out logic

**Consequences**:
- If process dies, everything stops - no worker redundancy
- Cannot scale horizontally, but rate limits prevent this anyway
- Graceful shutdown is critical to avoid data loss

---

## Why append-only SQLite, no mutations?

**Decision**: Every row is immutable after insertion. No UPDATE or DELETE statements.

**Rationale**:
- **Auditability**: Can reconstruct full history, debug data issues by inspecting raw payloads
- **Concurrency**: WAL mode supports concurrent readers, no lock contention from updates
- **Simplicity**: No versioning or soft-delete logic needed
- **Research quality**: Immutable data prevents accidental corruption during analysis

**Consequences**:
- Disk usage grows unbounded (mitigate: purge old data manually if needed)
- Errors must be logged as new rows, not fixed in place
- Duplicate websocket messages create duplicate rows (acceptable for research)

---

## Why structured JSON logging?

**Decision**: Log all events as JSON to stdout, no file rotation.

**Rationale**:
- **Parsing**: JSON is machine-readable for log aggregation tools
- **Stdout**: Let the environment handle log persistence (systemd, Docker, etc.)
- **Context**: Structured logs carry request_id, mint, horizon, timestamp in every line

**Alternatives considered**:
- Plain text: Easier to read with `tail -f`, but harder to parse programmatically
- File rotation: Adds complexity, risk of losing logs during rotation

**Consequences**:
- Logs are verbose, potentially large
- Human readability requires `jq` or similar tools
- Deployment must configure log capture (e.g., `tee` to file, systemd journal)

---

## Why no deduplication?

**Decision**: If PumpPortal sends duplicate migration events, both are inserted as separate rows.

**Rationale**:
- **Unknown semantics**: We don't know if duplicates are possible or what they mean
- **Correctness**: Deduplication requires a uniqueness assumption (mint? mint+pool? mint+timestamp?)
- **Observability**: Duplicates in the data reveal upstream issues
- **Fix in analysis**: Deduplication is trivial in SQL (`SELECT DISTINCT` or `GROUP BY`)

**Consequences**:
- If duplicates occur, they will inflate migration counts
- Analysis queries must deduplicate explicitly
- Disk usage may be higher if duplicates are common

---

## Why no historical backfill?

**Decision**: Collect only forward from deployment time, no attempt to fetch historical migrations.

**Rationale**:
- **Lookahead bias**: Historical data would require retroactive price/liquidity fetching, which may not reflect real-time availability
- **API limits**: Historical data is often paid-tier only or unavailable
- **Research validity**: Forward collection is cleaner methodologically

**Consequences**:
- No data before deployment date
- Results require ≥30 days of collection for meaningful distribution statistics
