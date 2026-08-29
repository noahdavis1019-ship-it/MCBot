"""Main entrypoint for EXP-001 data collector."""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

from mcbot.collector import MigrationCollector
from mcbot.db import init_db, insert_heartbeat, insert_migration
from mcbot.probe import QuoteProber
from mcbot.ratelimit import RateLimiter
from mcbot.scheduler import ObservationScheduler


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
            "timestamp": datetime.utcnow().isoformat(),
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
        self.ws_collector = MigrationCollector(self._on_migration)

    def _on_migration(self, payload: dict) -> None:
        """Handle migration event from websocket.

        Args:
            payload: Migration event payload from PumpPortal
        """
        try:
            # Extract fields from payload
            # Adjust field names based on actual PumpPortal format
            mint = payload.get("mint")
            symbol = payload.get("symbol")
            pool = payload.get("pool")
            timestamp = payload.get("timestamp") or payload.get("migrationTimestamp")

            if not mint:
                self.logger.warning("Migration missing mint", extra={"payload": payload})
                return

            # Convert timestamp to ISO 8601 UTC if needed
            if isinstance(timestamp, (int, float)):
                migration_ts_utc = datetime.fromtimestamp(timestamp).isoformat()
            elif isinstance(timestamp, str):
                migration_ts_utc = timestamp
            else:
                migration_ts_utc = datetime.utcnow().isoformat()

            # Insert migration into database
            insert_migration(
                conn=self.db,
                mint=mint,
                symbol=symbol,
                pool=pool,
                migration_ts_utc=migration_ts_utc,
                raw_payload=json.dumps(payload),
            )

            self.logger.info(
                "Migration recorded",
                extra={
                    "mint": mint,
                    "symbol": symbol,
                    "timestamp": migration_ts_utc,
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
