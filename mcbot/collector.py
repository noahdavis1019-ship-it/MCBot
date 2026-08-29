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

        # Load API key from environment
        load_dotenv()
        self._api_key = os.getenv("PUMPPORTAL_API_KEY")
        if not self._api_key:
            raise ValueError(
                "PUMPPORTAL_API_KEY not found in environment. "
                "See .env.example for setup instructions."
            )

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
        ws_url = f"wss://pumpportal.fun/api/data?api-key={self._api_key}"
        logger.info("Connecting to PumpPortal")

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
                        extra={"error": str(e), "message": message[:200]}
                    )

    async def _handle_message(self, message: str) -> None:
        """Parse and handle incoming websocket message.

        Args:
            message: Raw websocket message string
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON", extra={"error": str(e), "message": message[:200]})
            return

        # Detect message type - migration events typically have specific structure
        # Exact format depends on PumpPortal API - adjust as needed
        if not isinstance(data, dict):
            logger.warning("Non-dict message", extra={"message": message[:200]})
            return

        # Call migration handler
        # This assumes the entire message is a migration event
        # Adjust filtering logic based on actual PumpPortal format
        logger.info("Migration event received", extra={"mint": data.get("mint", "unknown")})
        self.on_migration(data)

    async def _backoff(self) -> None:
        """Wait with exponential backoff before reconnecting."""
        logger.warning(
            "Reconnecting after delay",
            extra={"delay_seconds": self._reconnect_delay}
        )
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)
