"""Main entrypoint for EXP-001 data collector."""

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from mcbot.collector import MigrationCollector
from mcbot.db import init_db, insert_heartbeat, insert_migration
from mcbot.helius import get_block_time
from mcbot.probe import QuoteProber
from mcbot.ratelimit import RateLimiter
from mcbot.scheduler import ObservationScheduler
from mcbot.timeutil import ts_to_utc_iso, utcnow_iso


class JSONFormatter(logging.Formatter):
    """Format logs as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON-formatted log string
        """
        log_obj = {
            "timestamp": utcnow_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "extra"):
            log_obj.update(record.extra)

        return json.dumps(log_obj)


def setup_logging() -> None:
    """Configure structured JSON logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


class Collector:
    """Main collector orchestrator."""

    def __init__(self, db_path: Path):
        """Initialize collector.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._shutdown_event = asyncio.Event()

        # Initialize database
        self.db = init_db(db_path)
        self.logger.info("Database initialized", extra={"path": str(db_path)})

        # Initialize components
        self.rate_limiter = RateLimiter()
        self.scheduler = ObservationScheduler(self.db, self.rate_limiter)
        self.prober = QuoteProber(self.db, self.rate_limiter)
        self.ws_collector = MigrationCollector(self._on_migration, self.db)

    async def _on_migration(self, payload: dict) -> None:
        """Handle migration event from websocket.

        Args:
            payload: Migration event dict with fields:
                - mint: Token mint address
                - pool: Pool identifier (e.g., "pump-amm")
                - migration_ts_utc: ISO 8601 UTC timestamp (client-side)
                - signature: Transaction signature (for chain anchoring)
        """
        try:
            # Extract fields from observed migration schema
            mint = payload.get("mint")
            pool = payload.get("pool")
            migration_ts_utc = payload.get("migration_ts_utc")
            signature = payload.get("signature")

            if not mint:
                self.logger.warning("Migration missing mint", extra={"payload": payload})
                return

            if not migration_ts_utc:
                self.logger.warning("Migration missing timestamp", extra={"payload": payload})
                return

            # Fetch chain-anchored blockTime from Helius
            block_ts_utc = None
            if signature:
                try:
                    block_time = await get_block_time(signature)
                    if block_time:
                        block_ts_utc = ts_to_utc_iso(block_time)
                        self.logger.debug(
                            "Fetched blockTime",
                            extra={"signature": signature, "block_ts_utc": block_ts_utc}
                        )
                except ValueError as e:
                    # Missing HELIUS_API_KEY
                    self.logger.warning(
                        "Helius API not configured",
                        extra={"error": str(e)}
                    )
                except Exception as e:
                    self.logger.error(
                        "Failed to fetch blockTime",
                        extra={"signature": signature, "error": str(e)}
                    )

            # Insert migration into database
            # Note: symbol is None - migration frames don't include token metadata
            insert_migration(
                conn=self.db,
                mint=mint,
                symbol=None,  # Not present in migration frames
                pool=pool,
                migration_ts_utc=migration_ts_utc,
                raw_payload=json.dumps(payload),
                signature=signature,
                block_ts_utc=block_ts_utc,
            )

            self.logger.info(
                "Migration recorded",
                extra={
                    "mint": mint,
                    "pool": pool,
                    "migration_ts_utc": migration_ts_utc,
                    "block_ts_utc": block_ts_utc,
                    "has_chain_anchor": block_ts_utc is not None,
                }
            )

            # Schedule observations
            self.scheduler.schedule_observations(mint, migration_ts_utc)

            # Maybe schedule quote probes (25% sample)
            self.prober.maybe_schedule_probes(mint, migration_ts_utc)

        except Exception as e:
            self.logger.error(
                "Error handling migration",
                extra={"error": str(e), "payload": payload}
            )

    async def _heartbeat_loop(self) -> None:
        """Insert heartbeat rows every 5 minutes."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(300)  # 5 minutes
                insert_heartbeat(self.db)
                self.logger.info("Heartbeat")
            except Exception as e:
                self.logger.error("Heartbeat error", extra={"error": str(e)})

    async def run(self) -> None:
        """Run collector until shutdown signal."""
        self.logger.info("Starting collector")

        # Set up signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start components
        tasks = [
            asyncio.create_task(self.ws_collector.start()),
            asyncio.create_task(self.scheduler.start()),
            asyncio.create_task(self.prober.start()),
            asyncio.create_task(self._heartbeat_loop()),
        ]

        # Wait for shutdown
        await self._shutdown_event.wait()

        # Stop components
        await self.ws_collector.stop()
        await self.scheduler.stop()
        await self.prober.stop()

        # Wait for tasks to finish
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.logger.info("Collector stopped")

    async def shutdown(self) -> None:
        """Trigger graceful shutdown."""
        self.logger.info("Shutdown signal received")
        self._shutdown_event.set()


def main() -> None:
    """Main entrypoint."""
    setup_logging()

    # Database path
    db_path = Path(__file__).parent.parent / "data" / "mcbot.db"

    # Run collector
    collector = Collector(db_path)
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
