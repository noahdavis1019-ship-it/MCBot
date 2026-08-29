"""Helius RPC client for fetching chain-anchored timestamps."""

import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")


async def get_block_time(signature: str) -> Optional[int]:
    """Fetch blockTime for a transaction signature using Helius RPC.

    Args:
        signature: Solana transaction signature

    Returns:
        Unix timestamp (seconds since epoch) or None if fetch fails

    Raises:
        ValueError: If HELIUS_API_KEY is not configured
    """
    if not HELIUS_API_KEY:
        raise ValueError("HELIUS_API_KEY not found in environment variables")

    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "json",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()

            # Check for RPC errors
            if "error" in data:
                logger.error(
                    "Helius RPC error",
                    extra={"signature": signature, "error": data["error"]},
                )
                return None

            # Extract blockTime from result
            result = data.get("result")
            if not result:
                logger.warning(
                    "Transaction not found",
                    extra={"signature": signature},
                )
                return None

            block_time = result.get("blockTime")
            if block_time is None:
                logger.warning(
                    "blockTime missing in transaction",
                    extra={"signature": signature},
                )
                return None

            return block_time

    except httpx.HTTPStatusError as e:
        logger.error(
            "Helius HTTP error",
            extra={"signature": signature, "status": e.response.status_code},
        )
        return None
    except httpx.RequestError as e:
        logger.error(
            "Helius request error",
            extra={"signature": signature, "error": str(e)},
        )
        return None
    except Exception as e:
        logger.error(
            "Unexpected error fetching blockTime",
            extra={"signature": signature, "error": str(e), "type": type(e).__name__},
        )
        return None
