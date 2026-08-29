"""Observation scheduler for DexScreener data collection."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from heapq import heappop, heappush
from time import time
from typing import Protocol

import httpx

from mcbot.ratelimit import RateLimiter
from mcbot.timeutil import ts_to_utc_iso, utcnow_iso

logger = logging.getLogger(__name__)

# Observation horizons in minutes
HORIZONS = [
    ("1m", 1),
    ("5m", 5),
    ("15m", 15),
    ("30m", 30),
    ("1h", 60),
    ("4h", 240),
    ("24h", 1440),
]


@dataclass(order=True)
class ScheduledObservation:
    """A scheduled observation task."""

    scheduled_ts: float  # Unix timestamp when observation should run
    mint: str
    horizon_label: str
    migration_ts: str  # ISO 8601 UTC
    obs_id: int = 0  # Database row ID (0 for not yet persisted)


class Database(Protocol):
    """Database protocol for scheduler."""

    def insert_observation(
        self,
        mint: str,
        horizon_label: str,
        scheduled_ts_utc: str,
        actual_ts_utc: str,
        **kwargs
    ) -> int:
        """Insert observation into database."""
        ...


class ObservationScheduler:
    """Schedules and executes DexScreener observations at fixed horizons."""

    def __init__(self, db, rate_limiter: RateLimiter):
        """Initialize scheduler.

        Args:
            db: Database connection with insert_observation method
            rate_limiter: Global rate limiter
        """
        self.db = db
        self.rate_limiter = rate_limiter
        self._queue: list[ScheduledObservation] = []
        self._running = False
        self._http_client = None

    def schedule_observations(self, mint: str, migration_ts: str) -> None:
        """Schedule observations for all horizons after migration.

        Inserts all 7 observations as PENDING rows immediately to prevent data loss on restart.

        Args:
            mint: Token mint address
            migration_ts: Migration timestamp (ISO 8601 UTC)
        """
        from mcbot.db import insert_observation

        base_time = datetime.fromisoformat(migration_ts.replace("Z", "+00:00"))

        for label, minutes in HORIZONS:
            scheduled_dt = base_time + timedelta(minutes=minutes)
            scheduled_ts = scheduled_dt.timestamp()
            scheduled_ts_utc = scheduled_dt.isoformat()

            # Insert PENDING observation into database immediately
            obs_id = insert_observation(
                conn=self.db,
                mint=mint,
                horizon_label=label,
                scheduled_ts_utc=scheduled_ts_utc,
                actual_ts_utc=scheduled_ts_utc,  # Placeholder, updated when executed
                obs_status="PENDING",
                http_status=None,
                request_latency_ms=None,
                raw_payload=None,
            )

            # Add to in-memory queue
            obs = ScheduledObservation(
                scheduled_ts=scheduled_ts,
                mint=mint,
                horizon_label=label,
                migration_ts=migration_ts,
                obs_id=obs_id,
            )
            heappush(self._queue, obs)

            logger.info(
                "Scheduled observation",
                extra={
                    "mint": mint,
                    "horizon": label,
                    "scheduled_at": scheduled_dt.isoformat(),
                    "obs_id": obs_id,
                }
            )

    def load_pending_observations(self) -> None:
        """Load PENDING observations from database and build in-memory queue.

        Called on startup to resume work after restart. Expires observations that
        are >5 min overdue (restart gap).
        """
        from mcbot.db import expire_overdue_pending_observations, load_pending_observations
        from datetime import datetime, timezone

        # Expire observations that are >5 min overdue
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        cutoff_ts_utc = cutoff_time.isoformat()

        expired_count = expire_overdue_pending_observations(self.db, cutoff_ts_utc)
        if expired_count > 0:
            logger.warning(
                "Expired overdue pending observations from restart gap",
                extra={"count": expired_count}
            )

        # Load remaining PENDING observations
        pending_obs = load_pending_observations(self.db)

        for obs_data in pending_obs:
            scheduled_dt = datetime.fromisoformat(obs_data["scheduled_ts_utc"])
            scheduled_ts = scheduled_dt.timestamp()

            # Infer migration_ts from scheduled_ts (not perfect but close enough)
            # Find which horizon this is by scheduled time
            migration_ts = obs_data["scheduled_ts_utc"]  # Placeholder - won't be used

            obs = ScheduledObservation(
                scheduled_ts=scheduled_ts,
                mint=obs_data["mint"],
                horizon_label=obs_data["horizon_label"],
                migration_ts=migration_ts,
                obs_id=obs_data["id"],
            )
            heappush(self._queue, obs)

        if pending_obs:
            logger.info(
                "Loaded pending observations from database",
                extra={"count": len(pending_obs)}
            )

    async def start(self) -> None:
        """Start scheduler loop."""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)

        # Load pending observations from database (restart recovery)
        self.load_pending_observations()

        logger.info("Scheduler started")

        while self._running:
            await self._process_queue()
            await asyncio.sleep(1)  # Check queue every second

        await self._http_client.aclose()

    async def stop(self) -> None:
        """Stop scheduler gracefully."""
        logger.info("Stopping scheduler")
        self._running = False

    async def _process_queue(self) -> None:
        """Process due observations from queue."""
        from mcbot.db import update_observation_status

        now = time()

        while self._queue and self._queue[0].scheduled_ts <= now:
            obs = heappop(self._queue)

            # Check if observation is extremely late (>5 minutes past scheduled time)
            # This indicates chronic rate limit saturation - mark as MISSED_LATE
            lateness = now - obs.scheduled_ts
            if lateness > 300:  # 5 minutes
                actual_ts_utc = utcnow_iso()

                update_observation_status(
                    conn=self.db,
                    observation_id=obs.obs_id,
                    actual_ts_utc=actual_ts_utc,
                    obs_status="MISSED_LATE",
                    http_status=None,
                    request_latency_ms=0,
                    raw_payload=f"Observation skipped: {lateness:.0f}s late due to rate limits",
                )
                logger.warning(
                    "Observation skipped due to chronic rate limit",
                    extra={
                        "mint": obs.mint,
                        "horizon": obs.horizon_label,
                        "lateness_seconds": int(lateness),
                        "obs_id": obs.obs_id,
                    }
                )
                continue  # Skip to next observation

            # Check if we can acquire rate limit token
            if not self.rate_limiter.try_acquire_dexscreener():
                # Rate limited - put back in queue and wait
                heappush(self._queue, obs)
                logger.debug(
                    "Rate limited, deferring observation",
                    extra={"mint": obs.mint, "horizon": obs.horizon_label}
                )
                return

            # Execute observation
            await self._execute_observation(obs)

    async def _execute_observation(self, obs: ScheduledObservation) -> None:
        """Execute a single observation.

        Updates PENDING observation row to final status (OK | HTTP_ERROR).

        Args:
            obs: Scheduled observation to execute
        """
        from mcbot.db import update_observation_status

        actual_ts_utc = utcnow_iso()
        latency_start = time()

        try:
            # Call DexScreener API
            url = f"https://api.dexscreener.com/latest/dex/tokens/{obs.mint}"
            response = await self._http_client.get(url)

            latency_ms = int((time() - latency_start) * 1000)

            if response.status_code == 200:
                data = response.json()
                await self._update_successful_observation(
                    obs, actual_ts_utc, data, latency_ms
                )
            else:
                # HTTP error - update row with error status
                update_observation_status(
                    conn=self.db,
                    observation_id=obs.obs_id,
                    actual_ts_utc=actual_ts_utc,
                    obs_status="HTTP_ERROR",
                    http_status=response.status_code,
                    request_latency_ms=latency_ms,
                    raw_payload=response.text,
                )
                logger.warning(
                    "Observation HTTP error",
                    extra={
                        "mint": obs.mint,
                        "horizon": obs.horizon_label,
                        "status": response.status_code,
                        "obs_id": obs.obs_id,
                    }
                )

        except Exception as e:
            # Network or parsing error - update row with error
            latency_ms = int((time() - latency_start) * 1000)
            update_observation_status(
                conn=self.db,
                observation_id=obs.obs_id,
                actual_ts_utc=actual_ts_utc,
                obs_status="HTTP_ERROR",
                http_status=None,
                request_latency_ms=latency_ms,
                raw_payload=str(e),
            )
            logger.error(
                "Observation error",
                extra={
                    "mint": obs.mint,
                    "horizon": obs.horizon_label,
                    "error": str(e),
                    "obs_id": obs.obs_id,
                }
            )

    async def _update_successful_observation(
        self,
        obs: ScheduledObservation,
        actual_ts_utc: str,
        data: dict,
        latency_ms: int,
    ) -> None:
        """Parse DexScreener response and update observation.

        Updates PENDING observation row with market data.

        Args:
            obs: Scheduled observation
            actual_ts_utc: When observation was executed
            data: DexScreener API response
            latency_ms: Request latency in milliseconds
        """
        from mcbot.db import update_observation_status

        # DexScreener returns pairs array - find the Raydium pair
        pairs = data.get("pairs", [])
        if not pairs:
            # No pairs found - update with null data
            update_observation_status(
                conn=self.db,
                observation_id=obs.obs_id,
                actual_ts_utc=actual_ts_utc,
                obs_status="OK",
                http_status=200,
                request_latency_ms=latency_ms,
                raw_payload=json.dumps(data),
            )
            logger.info(
                "No pairs found for token",
                extra={"mint": obs.mint, "horizon": obs.horizon_label, "obs_id": obs.obs_id}
            )
            return

        # Take first pair (typically most liquid)
        pair = pairs[0]

        update_observation_status(
            conn=self.db,
            observation_id=obs.obs_id,
            actual_ts_utc=actual_ts_utc,
            obs_status="OK",
            price_usd=float(pair.get("priceUsd", 0)) if pair.get("priceUsd") else None,
            price_native=float(pair.get("priceNative", 0)) if pair.get("priceNative") else None,
            liquidity_usd=(
                float(pair.get("liquidity", {}).get("usd", 0)) if pair.get("liquidity") else None
            ),
            fdv=float(pair.get("fdv", 0)) if pair.get("fdv") else None,
            vol_5m=float(pair.get("volume", {}).get("m5", 0)) if pair.get("volume") else None,
            vol_1h=float(pair.get("volume", {}).get("h1", 0)) if pair.get("volume") else None,
            txns_buys_5m=(
                int(pair.get("txns", {}).get("m5", {}).get("buys", 0))
                if pair.get("txns")
                else None
            ),
            txns_sells_5m=(
                int(pair.get("txns", {}).get("m5", {}).get("sells", 0))
                if pair.get("txns")
                else None
            ),
            dex_id=pair.get("dexId"),
            http_status=200,
            request_latency_ms=latency_ms,
            raw_payload=json.dumps(data),
        )

        logger.info(
            "Observation completed",
            extra={
                "mint": obs.mint,
                "horizon": obs.horizon_label,
                "price_usd": pair.get("priceUsd"),
                "obs_id": obs.obs_id,
            }
        )
