"""Jupiter quote probe for execution cost estimation."""

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from heapq import heappop, heappush
from time import time
from typing import Protocol

import httpx

from mcbot.ratelimit import RateLimiter
from mcbot.timeutil import utcnow_iso

logger = logging.getLogger(__name__)

# Quote probe horizons in minutes (subset of observation horizons)
PROBE_HORIZONS = [
    ("1m", 1),
    ("5m", 5),
    ("15m", 15),
    ("1h", 60),
]

# Sampling parameters
SAMPLE_RATE = 0.25  # 25% of migrations
RNG_SEED = 42  # Fixed seed for reproducibility

# Jupiter quote parameters
SOL_MINT = "So11111111111111111111111111111111111111112"
PROBE_AMOUNT_LAMPORTS = 100_000_000  # 0.1 SOL
SLIPPAGE_BPS = 100  # 1% slippage tolerance


@dataclass(order=True)
class ScheduledProbe:
    """A scheduled quote probe task."""

    scheduled_ts: float  # Unix timestamp when probe should run
    mint: str
    horizon_label: str
    migration_ts: str  # ISO 8601 UTC


class Database(Protocol):
    """Database protocol for probe."""

    def insert_quote_probe(
        self,
        mint: str,
        probe_ts_utc: str,
        direction: str,
        in_amount_lamports: int,
        **kwargs
    ) -> int:
        """Insert quote probe into database."""
        ...


class QuoteProber:
    """Probes Jupiter for execution cost quotes on sampled migrations."""

    def __init__(self, db, rate_limiter: RateLimiter):
        """Initialize prober.

        Args:
            db: Database connection with insert_quote_probe method
            rate_limiter: Global rate limiter
        """
        self.db = db
        self.rate_limiter = rate_limiter
        self._queue: list[ScheduledProbe] = []
        self._running = False
        self._http_client = None
        self._rng = random.Random(RNG_SEED)

    def maybe_schedule_probes(self, mint: str, migration_ts: str) -> None:
        """Schedule quote probes if migration is sampled.

        Args:
            mint: Token mint address
            migration_ts: Migration timestamp (ISO 8601 UTC)
        """
        # Sample 25% of migrations
        if self._rng.random() >= SAMPLE_RATE:
            logger.debug("Migration not sampled for probes", extra={"mint": mint})
            return

        base_time = datetime.fromisoformat(migration_ts.replace("Z", "+00:00"))

        for label, minutes in PROBE_HORIZONS:
            scheduled_dt = base_time + timedelta(minutes=minutes)
            scheduled_ts = scheduled_dt.timestamp()

            probe = ScheduledProbe(
                scheduled_ts=scheduled_ts,
                mint=mint,
                horizon_label=label,
                migration_ts=migration_ts,
            )
            heappush(self._queue, probe)

            logger.info(
                "Scheduled quote probe",
                extra={
                    "mint": mint,
                    "horizon": label,
                    "scheduled_at": scheduled_dt.isoformat(),
                }
            )

    async def start(self) -> None:
        """Start prober loop."""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)

        logger.info("Quote prober started", extra={"sample_rate": SAMPLE_RATE, "seed": RNG_SEED})

        while self._running:
            await self._process_queue()
            await asyncio.sleep(1)  # Check queue every second

        await self._http_client.aclose()

    async def stop(self) -> None:
        """Stop prober gracefully."""
        logger.info("Stopping quote prober")
        self._running = False

    async def _process_queue(self) -> None:
        """Process due probes from queue."""
        now = time()

        while self._queue and self._queue[0].scheduled_ts <= now:
            probe = heappop(self._queue)

            # Check if we can acquire rate limit token
            if not self.rate_limiter.try_acquire_jupiter():
                # Rate limited - put back in queue and wait
                heappush(self._queue, probe)
                logger.debug(
                    "Rate limited, deferring probe",
                    extra={"mint": probe.mint, "horizon": probe.horizon_label}
                )
                return

            # Execute probe (bidirectional: SOL->TOKEN and TOKEN->SOL)
            await self._execute_probe(probe)

    async def _execute_probe(self, probe: ScheduledProbe) -> None:
        """Execute bidirectional quote probe.

        Args:
            probe: Scheduled probe to execute
        """
        probe_ts_utc = utcnow_iso()

        # SOL -> TOKEN quote
        await self._quote_swap(
            mint=probe.mint,
            probe_ts_utc=probe_ts_utc,
            direction="SOL->TOKEN",
            input_mint=SOL_MINT,
            output_mint=probe.mint,
            amount=PROBE_AMOUNT_LAMPORTS,
        )

        # Wait for rate limit before second quote
        await self.rate_limiter.acquire_jupiter()

        # TOKEN -> SOL quote
        # For this direction, we need to estimate token amount from first quote
        # For simplicity, we'll use a fixed approach: quote with 1 token unit
        # This is imprecise but consistent - the amount will be recorded in the DB
        await self._quote_swap(
            mint=probe.mint,
            probe_ts_utc=probe_ts_utc,
            direction="TOKEN->SOL",
            input_mint=probe.mint,
            output_mint=SOL_MINT,
            amount=1_000_000,  # Arbitrary token amount, varies by token decimals
        )

    async def _quote_swap(
        self,
        mint: str,
        probe_ts_utc: str,
        direction: str,
        input_mint: str,
        output_mint: str,
        amount: int,
    ) -> None:
        """Get quote from Jupiter API.

        Args:
            mint: Token mint address (for DB record)
            probe_ts_utc: Probe timestamp
            direction: "SOL->TOKEN" or "TOKEN->SOL"
            input_mint: Input token mint
            output_mint: Output token mint
            amount: Input amount in smallest units
        """
        latency_start = time()

        try:
            # Jupiter quote API v6
            url = "https://quote-api.jup.ag/v6/quote"
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": SLIPPAGE_BPS,
            }

            response = await self._http_client.get(url, params=params)
            latency_ms = int((time() - latency_start) * 1000)

            if response.status_code == 200:
                data = response.json()
                out_amount = int(data.get("outAmount", 0))
                price_impact_pct = float(data.get("priceImpactPct", 0.0))
                route_plan = json.dumps(data.get("routePlan", []))

                self.db.insert_quote_probe(
                    mint=mint,
                    probe_ts_utc=probe_ts_utc,
                    direction=direction,
                    in_amount_lamports=amount,
                    out_amount=out_amount,
                    price_impact_pct=price_impact_pct,
                    route_plan_json=route_plan,
                    http_status=200,
                    request_latency_ms=latency_ms,
                    raw_payload=json.dumps(data),
                )

                logger.info(
                    "Quote probe completed",
                    extra={
                        "mint": mint,
                        "direction": direction,
                        "out_amount": out_amount,
                        "price_impact": price_impact_pct,
                    }
                )
            else:
                # HTTP error
                self.db.insert_quote_probe(
                    mint=mint,
                    probe_ts_utc=probe_ts_utc,
                    direction=direction,
                    in_amount_lamports=amount,
                    out_amount=None,
                    price_impact_pct=None,
                    route_plan_json=None,
                    http_status=response.status_code,
                    request_latency_ms=latency_ms,
                    raw_payload=response.text,
                )

                logger.warning(
                    "Quote probe HTTP error",
                    extra={
                        "mint": mint,
                        "direction": direction,
                        "status": response.status_code,
                    }
                )

        except Exception as e:
            # Network or parsing error
            latency_ms = int((time() - latency_start) * 1000)
            self.db.insert_quote_probe(
                mint=mint,
                probe_ts_utc=probe_ts_utc,
                direction=direction,
                in_amount_lamports=amount,
                out_amount=None,
                price_impact_pct=None,
                route_plan_json=None,
                http_status=0,
                request_latency_ms=latency_ms,
                raw_payload=str(e),
            )

            logger.error(
                "Quote probe error",
                extra={
                    "mint": mint,
                    "direction": direction,
                    "error": str(e),
                }
            )
