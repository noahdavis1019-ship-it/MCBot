"""Unit tests for token bucket rate limiter."""

import asyncio
import time

import pytest

from mcbot.ratelimit import RateLimiter, TokenBucket


def test_token_bucket_initial_capacity():
    """Test that token bucket starts at full capacity."""
    bucket = TokenBucket(rate=1.0, capacity=10)
    assert bucket.tokens_available() == 10.0


def test_token_bucket_try_consume():
    """Test non-blocking token consumption."""
    bucket = TokenBucket(rate=1.0, capacity=10)

    # Should succeed
    assert bucket.try_consume(5) is True
    assert abs(bucket.tokens_available() - 5.0) < 0.01

    # Should succeed
    assert bucket.try_consume(5) is True
    assert abs(bucket.tokens_available() - 0.0) < 0.01

    # Should fail - no tokens left
    assert bucket.try_consume(1) is False


def test_token_bucket_refill():
    """Test that tokens refill at correct rate."""
    bucket = TokenBucket(rate=10.0, capacity=10)  # 10 tokens per second

    # Consume all tokens
    assert bucket.try_consume(10) is True
    assert abs(bucket.tokens_available() - 0.0) < 0.01

    # Wait 0.5 seconds, should have ~5 tokens
    time.sleep(0.5)
    tokens = bucket.tokens_available()
    assert 4.0 <= tokens <= 6.0  # Allow some timing variance

    # Wait another 0.5 seconds, should be back at capacity
    time.sleep(0.5)
    tokens = bucket.tokens_available()
    assert 9.0 <= tokens <= 10.0


def test_token_bucket_capacity_limit():
    """Test that tokens don't exceed capacity."""
    bucket = TokenBucket(rate=1.0, capacity=10)

    # Start at full capacity
    assert bucket.tokens_available() == 10.0

    # Wait and check - should still be at capacity
    time.sleep(1.0)
    assert bucket.tokens_available() == 10.0


@pytest.mark.asyncio
async def test_token_bucket_consume_blocking():
    """Test blocking consumption waits for tokens."""
    bucket = TokenBucket(rate=10.0, capacity=10)

    # Consume all tokens
    assert bucket.try_consume(10) is True

    # Consume 1 more - should block until refill
    start = time.time()
    await bucket.consume(1)
    elapsed = time.time() - start

    # Should have waited ~0.1 seconds for 1 token at rate 10/sec
    assert 0.05 <= elapsed <= 0.2


@pytest.mark.asyncio
async def test_token_bucket_burst():
    """Test that bucket handles burst correctly."""
    bucket = TokenBucket(rate=1.0, capacity=10)

    # Consume burst up to capacity
    for _ in range(10):
        assert bucket.try_consume(1) is True

    # Next request should fail
    assert bucket.try_consume(1) is False


@pytest.mark.asyncio
async def test_rate_limiter_dexscreener():
    """Test DexScreener rate limiter."""
    limiter = RateLimiter()

    # Should succeed immediately
    assert limiter.try_acquire_dexscreener() is True

    # Consume burst capacity
    for _ in range(9):
        assert limiter.try_acquire_dexscreener() is True

    # Should fail after burst exhausted
    assert limiter.try_acquire_dexscreener() is False


@pytest.mark.asyncio
async def test_rate_limiter_jupiter():
    """Test Jupiter rate limiter."""
    limiter = RateLimiter()

    # Should succeed immediately
    assert limiter.try_acquire_jupiter() is True

    # Consume burst capacity
    for _ in range(4):
        assert limiter.try_acquire_jupiter() is True

    # Should fail after burst exhausted
    assert limiter.try_acquire_jupiter() is False


@pytest.mark.asyncio
async def test_rate_limiter_independent():
    """Test that DexScreener and Jupiter rate limits are independent."""
    limiter = RateLimiter()

    # Exhaust DexScreener
    for _ in range(10):
        assert limiter.try_acquire_dexscreener() is True

    # Jupiter should still work
    assert limiter.try_acquire_jupiter() is True
