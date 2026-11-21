"""Calendar and company-fiscal period resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .timeutil import TimeError, iso_utc, parse_utc


MONTHS_PER_QUARTER = 3


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TimeError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TimeError(f"{field} must be an integer") from exc
    if str(value).strip() not in {str(result), f"{result:d}"} and not isinstance(value, int):
        # Reject strings such as "1.5" and "2026x" while allowing "01".
        if not (isinstance(value, str) and value.strip().isdigit()):
            raise TimeError(f"{field} must be an integer")
    return result


def _month_range(year: int, month: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if month == 12:
        end_year, end_month = year + 1, 1
    else:
        end_year, end_month = year, month + 1
    # Zoneinfo accepts the naive constructor and resolves DST on conversion.
    start_local = datetime(year, month, 1, tzinfo=tz)
    end_local = datetime(end_year, end_month, 1, tzinfo=tz)
    return start_local.astimezone(tz), end_local.astimezone(tz)


def parse_period(period: Any, timezone_name: str, fiscal_start_month: int) -> dict[str, Any]:
    if period is None:
        return {
            "type": "none",
            "label": "all_dates",
            "start": None,
            "end": None,
            "timezone": timezone_name,
        }
    if not isinstance(period, dict):
        raise TimeError("period must be an object")
    kind = period.get("type")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:
        raise TimeError(f"configured timezone is invalid: {timezone_name}") from exc
    if fiscal_start_month not in range(1, 13):
        raise TimeError("configured fiscal year start month must be 1-12")

    if kind == "calendar_month":
        year = _integer(period.get("year"), "period.year")
        month = _integer(period.get("month"), "period.month")
        if not 1 <= year <= 9999 or not 1 <= month <= 12:
            raise TimeError("calendar_month year/month is outside the supported range")
        start, end = _month_range(year, month, tz)
        label = f"{year:04d}-{month:02d}"
    elif kind == "calendar_quarter":
        year = _integer(period.get("year"), "period.year")
        quarter = _integer(period.get("quarter"), "period.quarter")
        if not 1 <= year <= 9999 or not 1 <= quarter <= 4:
            raise TimeError("calendar_quarter year/quarter is outside the supported range")
        month = (quarter - 1) * MONTHS_PER_QUARTER + 1
        start, end = _month_range(year, month, tz)
        if month + 2 == 12:
            end = _month_range(year, 12, tz)[1]
        label = f"{year:04d}-Q{quarter}"
    elif kind == "calendar_year":
        year = _integer(period.get("year"), "period.year")
        if not 1 <= year <= 9999:
            raise TimeError("calendar_year is outside the supported range")
        start, end = _month_range(year, 1, tz)
        label = f"{year:04d}"
    elif kind == "fiscal_quarter":
        fiscal_year = _integer(period.get("fiscal_year"), "period.fiscal_year")
        quarter = _integer(period.get("fiscal_quarter"), "period.fiscal_quarter")
        if not 1 <= fiscal_year <= 9999 or not 1 <= quarter <= 4:
            raise TimeError("fiscal_quarter year/quarter is outside the supported range")
        start_month = (fiscal_start_month + (quarter - 1) * MONTHS_PER_QUARTER - 1) % 12 + 1
        # FY labels refer to the year in which the fiscal year ends. For a
        # January-aligned year, that is the start calendar year; otherwise the
        # fiscal year starts in the preceding calendar year. Quarters that wrap
        # past December fall after New Year in the label year.
        if fiscal_start_month == 1 or start_month < fiscal_start_month:
            calendar_year = fiscal_year
        else:
            calendar_year = fiscal_year - 1
        start = _month_range(calendar_year, start_month, tz)[0]
        # The quarter ends at the start of the month following its final month.
        end_month = (start_month + 2) % 12 + 1
        end_year = calendar_year + (1 if end_month <= start_month else 0)
        end = datetime(end_year, end_month, 1, tzinfo=tz).astimezone(tz)
        label = f"FY{fiscal_year:04d}-Q{quarter}"
    elif kind == "date_range":
        start = parse_utc(period.get("start"), "period.start")
        end = parse_utc(period.get("end"), "period.end")
        if start >= end:
            raise TimeError("period.start must be before period.end")
        label = f"{start.date().isoformat()}..{end.date().isoformat()}"
    else:
        raise TimeError(
            "period.type must be calendar_month, calendar_quarter, calendar_year, "
            "fiscal_quarter, or date_range"
        )
    return {
        "type": kind,
        "label": label,
        "start": iso_utc(start),
        "end": iso_utc(end),
        "timezone": timezone_name,
    }


__all__ = ["parse_period"]
