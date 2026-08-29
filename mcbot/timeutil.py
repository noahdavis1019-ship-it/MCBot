"""Timezone-aware datetime utilities.

All timestamps in the database are UTC with explicit timezone (+00:00).
Never use datetime.utcnow() - it returns naive datetime.
"""

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Get current UTC time as ISO 8601 string with timezone.

    Returns:
        ISO 8601 string ending with +00:00 (e.g., "2026-08-29T12:34:56.789012+00:00")
    """
    return datetime.now(timezone.utc).isoformat()


def ts_to_utc_iso(epoch_seconds: float) -> str:
    """Convert Unix timestamp to UTC ISO 8601 string with timezone.

    Args:
        epoch_seconds: Unix timestamp (seconds since epoch)

    Returns:
        ISO 8601 string ending with +00:00
    """
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()
