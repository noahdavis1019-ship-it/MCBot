# System State

Last updated: 2026-08-29

## What Runs

- **EXP-001 Collector**: RUNNING (pending initial deployment)
  - PumpPortal websocket connection for migration events
  - DexScreener observation scheduler (7 horizons: 1m, 5m, 15m, 30m, 1h, 4h, 24h)
  - Jupiter quote probe (25% sample, bidirectional quotes at 1m/5m/15m/1h)
  - Heartbeat row every 5 minutes
  - Graceful shutdown on SIGTERM/SIGINT

## What Does Not Run

- No trading logic
- No strategy execution
- No wallet operations
- No private key handling
- No transaction construction
- No feature extraction or ML
- No dashboards or web interfaces
- No alerting or notification systems

## Infrastructure

- **Database**: SQLite at `data/mcbot.db`, versioned schema
- **Process**: Single Python process, no background workers
- **Logging**: Structured JSON logs to stdout
- **Rate Limits**:
  - PumpPortal: 1 persistent websocket, exponential backoff reconnect
  - DexScreener: ~60 requests/minute global budget
  - Jupiter: 1 request/second global budget

## Known Gaps

- Initial deployment: No historical data, forward-collected only from launch time
- Late observations: If rate limit budget is exhausted, observations may be delayed or missed - all logged explicitly
- WebSocket drops: Reconnection logic implemented but migration events during disconnect window will be lost
- Clock skew: Horizon scheduling assumes monotonic UTC clock
- No deduplication: Duplicate websocket messages (if any) will create duplicate rows

## Data Location

- `data/mcbot.db` - Primary SQLite database
- `data/mcbot.db-wal`, `data/mcbot.db-shm` - WAL mode files

## Monitoring

Check collection health:
```sql
-- Recent heartbeats (should be every 5 min)
SELECT * FROM observations WHERE http_status IS NULL ORDER BY actual_ts DESC LIMIT 10;

-- Migration event rate
SELECT COUNT(*), DATE(migration_ts_utc) as day FROM migrations GROUP BY day;

-- Observation completion rate by horizon
SELECT horizon_label,
       COUNT(*) as total,
       SUM(CASE WHEN http_status >= 200 AND http_status < 300 THEN 1 ELSE 0 END) as success,
       SUM(CASE WHEN http_status IS NULL THEN 1 ELSE 0 END) as missed
FROM observations
GROUP BY horizon_label;
```

## Recovery Procedures

- Database corruption: Restore from `.db-backup` if available, otherwise restart collection
- Stuck process: SIGTERM for graceful shutdown, SIGKILL only as last resort
- Rate limit ban: Wait for ban expiry (typically 1-24h), restart collector
- Disk full: Clear old logs, consider purging ancient migration data if necessary

## Version

Schema version: 1 (see DATA_DICTIONARY.md for changelog)
