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

        Args:
            mint: Token mint address
            migration_ts: Migration timestamp (ISO 8601 UTC)
        """
        base_time = datetime.fromisoformat(migration_ts.replace("Z", "+00:00"))

        for label, minutes in HORIZONS:
            scheduled_dt = base_time + timedelta(minutes=minutes)
            scheduled_ts = scheduled_dt.timestamp()

            obs = ScheduledObservation(
                scheduled_ts=scheduled_ts,
                mint=mint,
                horizon_label=label,
                migration_ts=migration_ts,
            )
            heappush(self._queue, obs)

            logger.info(
                "Scheduled observation",
                extra={
                    "mint": mint,
                    "horizon": label,
                    "scheduled_at": scheduled_dt.isoformat(),
                }
            )

    async def start(self) -> None:
        """Start scheduler loop."""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)

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
        now = time()

        while self._queue and self._queue[0].scheduled_ts <= now:
            obs = heappop(self._queue)

            # Check if observation is extremely late (>5 minutes past scheduled time)
            # This indicates chronic rate limit saturation - log as missed
            lateness = now - obs.scheduled_ts
            if lateness > 300:  # 5 minutes
                scheduled_ts_utc = datetime.fromtimestamp(obs.scheduled_ts).isoformat()
                actual_ts_utc = datetime.utcnow().isoformat()

                self.db.insert_observation(
                    mint=obs.mint,
                    horizon_label=obs.horizon_label,
                    scheduled_ts_utc=scheduled_ts_utc,
                    actual_ts_utc=actual_ts_utc,
                    http_status=429,  # Too Many Requests - rate limited
                    request_latency_ms=0,
                    raw_payload=f"Observation skipped: {lateness:.0f}s late due to rate limits",
                )
                logger.warning(
                    "Observation skipped due to chronic rate limit",
                    extra={
                        "mint": obs.mint,
                        "horizon": obs.horizon_label,
                        "lateness_seconds": int(lateness)
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

        Args:
            obs: Scheduled observation to execute
        """
        scheduled_ts_utc = datetime.fromtimestamp(obs.scheduled_ts).isoformat()
        actual_ts_utc = datetime.utcnow().isoformat()

        latency_start = time()

        try:
            # Call DexScreener API
            url = f"https://api.dexscreener.com/latest/dex/tokens/{obs.mint}"
            response = await self._http_client.get(url)

            latency_ms = int((time() - latency_start) * 1000)

            if response.status_code == 200:
                data = response.json()
                await self._insert_successful_observation(
                    obs, scheduled_ts_utc, actual_ts_utc, data, latency_ms
                )
            else:
                # HTTP error - insert row with error status
                self.db.insert_observation(
                    mint=obs.mint,
                    horizon_label=obs.horizon_label,
                    scheduled_ts_utc=scheduled_ts_utc,
                    actual_ts_utc=actual_ts_utc,
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
                    }
                )

        except Exception as e:
            # Network or parsing error - insert row with error
            latency_ms = int((time() - latency_start) * 1000)
            self.db.insert_observation(
                mint=obs.mint,
                horizon_label=obs.horizon_label,
                scheduled_ts_utc=scheduled_ts_utc,
                actual_ts_utc=actual_ts_utc,
                http_status=0,  # 0 indicates network error
                request_latency_ms=latency_ms,
                raw_payload=str(e),
            )
            logger.error(
                "Observation error",
                extra={
                    "mint": obs.mint,
                    "horizon": obs.horizon_label,
                    "error": str(e),
                }
            )

    async def _insert_successful_observation(
        self,
        obs: ScheduledObservation,
        scheduled_ts_utc: str,
        actual_ts_utc: str,
        data: dict,
        latency_ms: int,
    ) -> None:
        """Parse DexScreener response and insert observation.

        Args:
            obs: Scheduled observation
            scheduled_ts_utc: When observation was scheduled
            actual_ts_utc: When observation was executed
            data: DexScreener API response
            latency_ms: Request latency in milliseconds
        """
        # DexScreener returns pairs array - find the Raydium pair
        pairs = data.get("pairs", [])
        if not pairs:
            # No pairs found - insert null observation
            self.db.insert_observation(
                mint=obs.mint,
                horizon_label=obs.horizon_label,
                scheduled_ts_utc=scheduled_ts_utc,
                actual_ts_utc=actual_ts_utc,
                http_status=200,
                request_latency_ms=latency_ms,
                raw_payload=json.dumps(data),
            )
            logger.info(
                "No pairs found for token",
                extra={"mint": obs.mint, "horizon": obs.horizon_label}
            )
            return

        # Take first pair (typically most liquid)
        pair = pairs[0]

        self.db.insert_observation(
            mint=obs.mint,
            horizon_label=obs.horizon_label,
            scheduled_ts_utc=scheduled_ts_utc,
            actual_ts_utc=actual_ts_utc,
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
            }
        )
