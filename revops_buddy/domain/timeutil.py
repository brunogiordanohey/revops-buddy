"""Strict UTC time helpers shared by the deterministic domain tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class TimeError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: Any, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TimeError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimeError(f"{field} is not a valid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise TimeError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise TimeError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = ["TimeError", "iso_utc", "parse_utc", "utc_now"]
