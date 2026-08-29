"""Token bucket rate limiter for API calls."""

import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Token bucket rate limiter.

    Tokens refill at a constant rate up to a maximum capacity.
    Each operation consumes one token. If no tokens available, caller must wait.
    """

    rate: float  # tokens per second
    capacity: int  # maximum tokens
    _tokens: float = 0.0
    _last_update: float = 0.0

    def __post_init__(self):
        """Initialize token count and timestamp."""
        self._tokens = float(self.capacity)
        self._last_update = time.monotonic()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_update = now

    def try_consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens without blocking.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False if insufficient tokens
        """
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def consume(self, tokens: int = 1) -> None:
        """Consume tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to consume
        """
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            # Wait until we have enough tokens
            deficit = tokens - self._tokens
            wait_time = deficit / self.rate
            await asyncio.sleep(wait_time)

    def tokens_available(self) -> float:
        """Get current token count.

        Returns:
            Number of tokens currently available
        """
        self._refill()
        return self._tokens

    def time_until_token(self) -> float:
        """Get time until next token is available.

        Returns:
            Seconds until at least one token is available (0 if tokens available now)
        """
        self._refill()
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) / self.rate


class RateLimiter:
    """Global rate limiter for multiple API endpoints."""

    def __init__(self):
        """Initialize rate limiters for each API.

        DexScreener: ~60 requests/minute (1 per second with burst capacity)
        Jupiter: 0.5 request/second (30 per minute keyless, with small burst)
        """
        # DexScreener: 1 req/sec, allow burst of 10
        self.dexscreener = TokenBucket(rate=1.0, capacity=10)

        # Jupiter: 0.5 req/sec (keyless), allow burst of 3
        self.jupiter = TokenBucket(rate=0.5, capacity=3)

    async def acquire_dexscreener(self) -> None:
        """Acquire rate limit token for DexScreener API call."""
        await self.dexscreener.consume(1)

    async def acquire_jupiter(self) -> None:
        """Acquire rate limit token for Jupiter API call."""
        await self.jupiter.consume(1)

    def try_acquire_dexscreener(self) -> bool:
        """Try to acquire DexScreener token without blocking.

        Returns:
            True if token acquired, False if rate limited
        """
        return self.dexscreener.try_consume(1)

    def try_acquire_jupiter(self) -> bool:
        """Try to acquire Jupiter token without blocking.

        Returns:
            True if token acquired, False if rate limited
        """
        return self.jupiter.try_consume(1)
