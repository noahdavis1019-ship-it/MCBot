# Design Decisions

## Append-Only Schema with One Exception

**Date:** 2026-08-29
**Context:** LaunchAgent restart data loss prevention

### Decision

The EXP-001 database schema is **append-only** with **one documented exception**:

**Allowed UPDATE:** Observations table, PENDING → final status transition.

### Rationale

**Problem:**
Running collector as macOS LaunchAgent with `KeepAlive=true` means automatic restart on crash. The original design used an in-memory heap queue for scheduled observations. A restart would:

1. Destroy ~800 pending observations (7 horizons × ~115 migrations/day)
2. Resume heartbeats within seconds
3. Coverage report certifies 100% uptime over holey data
4. **Silent data loss** - no way to detect the gap

**Solution:**
Persist observations immediately at migration time with `obs_status='PENDING'`. Scheduler loads PENDING rows on startup and updates them in-place when executed.

**Why UPDATE is safe here:**

1. **Single writer** - Only the observation scheduler updates these rows
2. **State machine** - PENDING → {OK, MISSED_LATE, HTTP_ERROR} is irreversible
3. **Atomic** - Each observation updated exactly once
4. **Restart recovery** - On startup:
   - Load all PENDING observations
   - Expire observations >5 min overdue → MISSED_LATE with reason "restart_gap"
   - Resume remaining observations

**Append-only preserved for:**
- Migrations (never UPDATE)
- Parse failures (never UPDATE)
- Quote probes (never UPDATE)
- Heartbeats (never UPDATE)

### Implementation

**At migration time:**
```python
# Insert all 7 horizons as PENDING immediately
for label, minutes in HORIZONS:
    obs_id = insert_observation(
        mint=mint,
        horizon_label=label,
        scheduled_ts_utc=scheduled_ts_utc,
        actual_ts_utc=scheduled_ts_utc,  # Placeholder
        obs_status="PENDING",
        http_status=None,
    )
```

**On startup:**
```python
# Expire overdue observations (>5 min late)
expire_overdue_pending_observations(db, cutoff_ts_utc)

# Load remaining PENDING observations into queue
pending_obs = load_pending_observations(db)
for obs in pending_obs:
    heappush(queue, obs)
```

**On execution:**
```python
# Update PENDING → OK with market data
update_observation_status(
    observation_id=obs.obs_id,
    actual_ts_utc=actual_ts_utc,
    obs_status="OK",
    price_usd=price_usd,
    # ... other fields
)
```

### Test Coverage

**Test:** `tests/test_restart_recovery.py::test_pending_observations_survive_restart`

Acceptance gate for 24h run:
1. Start collector
2. Trigger migration (inserts 7 PENDING observations)
3. Kill process mid-run
4. Restart collector
5. Assert: PENDING observations reload and execute
6. Assert: Overdue observations (>5 min) marked MISSED_LATE

### Alternatives Considered

**Option 1: Separate pending_observations table**
❌ Rejected - Adds complexity, still needs UPDATE or dual-write + cleanup

**Option 2: Reconstruct queue from migrations table on restart**
❌ Rejected - Can't distinguish completed vs pending observations

**Option 3: Accept data loss, rely on coverage report to detect gaps**
❌ Rejected - Defeats purpose of 24/7 collection. Gaps are measured, not repaired.

### Status

✅ **Implemented** (2026-08-29)
⏳ **Acceptance test pending**
