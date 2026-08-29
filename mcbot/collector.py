"""PumpPortal websocket collector for migration events."""

import asyncio
import json
import logging
import os
from collections.abc import Callable

import websockets
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

MAX_RECONNECT_DELAY = 300  # 5 minutes max backoff


class MigrationCollector:
    """Collects migration events from PumpPortal websocket.

    Maintains single persistent connection with exponential backoff reconnection.
    NEVER opens concurrent connections (PumpPortal bans for this).
    """

    def __init__(self, on_migration: Callable[[dict], None]):
        """Initialize collector.

        Args:
            on_migration: Callback function called for each migration event
        """
        self.on_migration = on_migration
        self._ws = None
        self._running = False
        self._reconnect_delay = 1.0

        # Load API key from environment (optional - will test keyless connection)
        load_dotenv()
        self._api_key = os.getenv("PUMPPORTAL_API_KEY")

    async def start(self) -> None:
        """Start collector with automatic reconnection."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error("Collector error", extra={"error": str(e), "type": type(e).__name__})
                if self._running:
                    await self._backoff()

    async def stop(self) -> None:
        """Stop collector gracefully."""
        logger.info("Stopping collector")
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_and_listen(self) -> None:
        """Connect to websocket and listen for messages."""
        # Build URL with optional API key
        if self._api_key:
            ws_url = f"wss://pumpportal.fun/api/data?api-key={self._api_key}"
        else:
            ws_url = "wss://pumpportal.fun/api/data"

        logger.info(
            "Connecting to PumpPortal",
            extra={"has_api_key": bool(self._api_key)}
        )

        async with websockets.connect(ws_url) as ws:
            self._ws = ws
            logger.info("Connected to PumpPortal")

            # Subscribe to migration events only
            subscribe_msg = {
                "method": "subscribeMigration"
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info("Subscribed to migration events")

            # Reset backoff on successful connection
            self._reconnect_delay = 1.0

            # Listen for messages
            async for message in ws:
                try:
                    await self._handle_message(message)
                except Exception as e:
                    logger.error(
                        "Error handling message",
                        extra={"error": str(e), "raw_frame": message[:200]}
                    )

    async def _handle_message(self, message: str) -> None:
        """Parse and handle incoming websocket message.

        Args:
            message: Raw websocket message string
        """
        from mcbot.db import insert_parse_failure
        from mcbot.timeutil import utcnow_iso

        received_ts = utcnow_iso()

        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON", extra={"error": str(e), "raw_frame": message[:200]})
            # This shouldn't have a db connection here yet, but will after recording
            # For now just log and return
            return

        # Validate message structure
        if not isinstance(data, dict):
            logger.warning("Non-dict message", extra={"raw_frame": message[:200]})
            return

        # Filter for migration events based on observed schema
        # Observed migration frame: {"signature": str, "mint": str, "txType": "migrate", "pool": str}
        if data.get("txType") == "migrate":
            # Validate required fields
            required_fields = ["signature", "mint", "pool"]
            missing_fields = [f for f in required_fields if f not in data]

            if missing_fields:
                logger.warning(
                    "Migration frame missing fields",
                    extra={"missing": missing_fields, "raw_frame": message[:200]}
                )
                return

            # Valid migration - extract timestamp and route to callback
            migration_data = {
                "signature": data["signature"],
                "mint": data["mint"],
                "pool": data["pool"],
                "migration_ts_utc": received_ts,  # Use receive time as migration timestamp
            }
            self.on_migration(migration_data)
            logger.info(
                "Migration detected",
                extra={"mint": data["mint"], "pool": data["pool"]}
            )
        else:
            # Non-migration frame (token create, subscription confirm, etc.) - filter silently
            tx_type = data.get("txType", data.get("message", "unknown"))
            logger.debug("Non-migration frame filtered", extra={"type": tx_type})

    async def _backoff(self) -> None:
        """Wait with exponential backoff before reconnecting."""
        logger.warning(
            "Reconnecting after delay",
            extra={"delay_seconds": self._reconnect_delay}
        )
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)
