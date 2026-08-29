"""PumpPortal websocket collector for migration events."""

import asyncio
import json
import logging
import os
import sqlite3
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

    def __init__(self, on_migration: Callable[[dict], None], db: sqlite3.Connection | None = None):
        """Initialize collector.

        Args:
            on_migration: Callback function called for each migration event
            db: SQLite connection for parse failure logging (optional)
        """
        self.on_migration = on_migration
        self.db = db
        self._ws = None
        self._running = False
        self._reconnect_delay = 1.0
        self._ignored_frame_count = 0

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

    def get_and_reset_ignored_count(self) -> int:
        """Get count of ignored frames since last call and reset counter.

        Returns:
            Number of known-ignored frames (creates, subscription messages) received
        """
        count = self._ignored_frame_count
        self._ignored_frame_count = 0
        return count

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
        """Parse and handle incoming websocket message with three-way routing.

        Routes:
            1. MIGRATION (txType="migrate") → on_migration callback
            2. KNOWN_IGNORED (creates, subscription messages) → increment counter
            3. UNKNOWN → insert parse_failure row

        Args:
            message: Raw websocket message string
        """
        from mcbot.db import insert_parse_failure
        from mcbot.timeutil import utcnow_iso

        received_ts = utcnow_iso()

        # Parse JSON
        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON", extra={"error": str(e), "raw_frame": message[:200]})
            if self.db:
                insert_parse_failure(
                    conn=self.db,
                    received_ts=received_ts,
                    raw_frame=message,
                    reason=f"JSON decode error: {e}",
                )
            return

        # Validate message structure
        if not isinstance(data, dict):
            logger.warning("Non-dict message", extra={"raw_frame": message[:200]})
            if self.db:
                insert_parse_failure(
                    conn=self.db,
                    received_ts=received_ts,
                    raw_frame=message,
                    reason="Message is not a dict",
                )
            return

        # ROUTE 1: MIGRATION
        # Observed schema: {"signature": str, "mint": str, "txType": "migrate", "pool": str}
        if data.get("txType") == "migrate":
            required_fields = ["signature", "mint", "pool"]
            missing_fields = [f for f in required_fields if f not in data]

            if missing_fields:
                logger.warning(
                    "Migration frame missing fields",
                    extra={"missing": missing_fields, "raw_frame": message[:200]}
                )
                if self.db:
                    insert_parse_failure(
                        conn=self.db,
                        received_ts=received_ts,
                        raw_frame=message,
                        reason=f"Migration missing fields: {missing_fields}",
                    )
                return

            # Valid migration - route to callback
            migration_data = {
                "signature": data["signature"],
                "mint": data["mint"],
                "pool": data["pool"],
                "migration_ts_utc": received_ts,
            }
            self.on_migration(migration_data)
            logger.info(
                "Migration detected",
                extra={"mint": data["mint"], "pool": data["pool"]}
            )
            return

        # ROUTE 2: TOKEN CREATES
        # Observed schema: {bondingCurveKey, initialBuy, is_mayhem_mode, marketCapSol,
        #                   mint, name, pool, signature, solAmount, symbol, traderPublicKey,
        #                   txType, uri, vSolInBondingCurve, vTokensInBondingCurve}
        if data.get("txType") == "create":
            from mcbot.db import insert_creation

            # Increment counter for heartbeat (keep this for monitoring)
            self._ignored_frame_count += 1

            # Insert creation into database
            if self.db:
                try:
                    insert_creation(
                        conn=self.db,
                        mint=data.get("mint"),
                        signature=data.get("signature"),
                        recv_ts_utc=received_ts,
                        raw_payload=message,
                        name=data.get("name"),
                        symbol=data.get("symbol"),
                        uri=data.get("uri"),
                        bonding_curve_key=data.get("bondingCurveKey"),
                        trader_public_key=data.get("traderPublicKey"),
                        initial_buy=data.get("initialBuy"),
                        sol_amount=data.get("solAmount"),
                        market_cap_sol=data.get("marketCapSol"),
                        v_sol_in_bonding_curve=data.get("vSolInBondingCurve"),
                        v_tokens_in_bonding_curve=data.get("vTokensInBondingCurve"),
                        pool=data.get("pool"),
                        is_mayhem_mode=data.get("is_mayhem_mode"),
                    )
                    logger.debug("Token creation recorded", extra={"mint": data.get("mint")})
                except Exception as e:
                    logger.error("Failed to insert creation", extra={"error": str(e), "mint": data.get("mint")})
            return

        # Subscription confirmations ({"message": "Successfully subscribed..."})
        if "message" in data and isinstance(data["message"], str):
            self._ignored_frame_count += 1
            logger.debug("Subscription message ignored", extra={"msg": data["message"]})
            return

        # Error/warning messages ({"errors": "Invalid API key..."})
        if "errors" in data:
            self._ignored_frame_count += 1
            logger.debug("Error/warning frame ignored", extra={"errors": data["errors"]})
            return

        # ROUTE 3: UNKNOWN
        # Unrecognized frame structure - record as parse failure
        logger.warning("Unknown frame structure", extra={"raw_frame": message[:200]})
        if self.db:
            insert_parse_failure(
                conn=self.db,
                received_ts=received_ts,
                raw_frame=message,
                reason="Unknown frame structure (not migration, create, or control message)",
            )

    async def _backoff(self) -> None:
        """Wait with exponential backoff before reconnecting."""
        logger.warning(
            "Reconnecting after delay",
            extra={"delay_seconds": self._reconnect_delay}
        )
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)
