"""Durable storage for Angels snapshots, meetings, and transcripts."""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import JSON, Integer, String


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _as_utc_iso(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a timezone-aware UTC ISO string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware UTC ISO string")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    """Detach stored JSON from caller-owned dictionaries and lists."""
    return copy.deepcopy(value)


class Base(DeclarativeBase):
    pass


class SnapshotRow(Base):
    __tablename__ = "angels_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(255), index=True)
    captured_at: Mapped[str] = mapped_column(String(64), index=True)
    source_updated_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    complete: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    def as_dict(self) -> dict[str, Any]:
        return _json_copy(self.payload)


class Repository:
    """SQLAlchemy storage for immutable CRM snapshots."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("database_url must be a non-empty string")
        self.database_url = database_url
        engine_kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(database_url, **engine_kwargs)
        self._write_lock = threading.RLock()


    def save_snapshot(self, snapshot: dict) -> str:
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dict")
        payload = _json_copy(snapshot)
        snapshot_id = payload.get("snapshot_id") or str(uuid.uuid4())
        payload["snapshot_id"] = snapshot_id
        company_id = payload.get("company_id")
        if not isinstance(company_id, str) or not company_id:
            raise ValueError("snapshot.company_id is required")
        captured_at = _as_utc_iso(payload.get("captured_at"), "snapshot.captured_at")
        if captured_at is None:
            captured_at = utc_now()
        payload["captured_at"] = captured_at
        payload["source_updated_at"] = _as_utc_iso(
            payload.get("source_updated_at"), "snapshot.source_updated_at"
        )
        if "complete" in payload:
            complete = payload["complete"]
        elif "status" in payload:
            complete = payload["status"] == "complete"
        else:
            complete = True
        payload["complete"] = bool(complete)
        if "status" not in payload:
            payload["status"] = "complete" if complete else "incomplete"

        row = SnapshotRow(
            snapshot_id=snapshot_id,
            company_id=company_id,
            captured_at=captured_at,
            source_updated_at=payload["source_updated_at"],
            complete=payload["complete"],
            payload=payload,
        )
        with self._write_lock, Session(self.engine) as session:
            existing = session.get(SnapshotRow, snapshot_id)
            if existing is not None:
                raise ValueError(f"snapshot_id already exists: {snapshot_id}")
            session.add(row)
            session.commit()
        return snapshot_id


    def latest_snapshot(self) -> dict | None:
        with Session(self.engine) as session:
            stmt = (
                select(SnapshotRow)
                .where(SnapshotRow.complete == True)  # noqa: E712 - SQLAlchemy expression
                .order_by(SnapshotRow.captured_at.desc(), SnapshotRow.snapshot_id.desc())
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return row.as_dict() if row is not None else None


    def list_snapshots(self) -> list[dict]:
        with Session(self.engine) as session:
            stmt = select(SnapshotRow).order_by(
                SnapshotRow.captured_at.desc(), SnapshotRow.snapshot_id.desc()
            )
            return [row.as_dict() for row in session.scalars(stmt).all()]


    def get_snapshot(self, snapshot_id: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(SnapshotRow, snapshot_id)
            return row.as_dict() if row is not None else None


    def initialize(self) -> None:
        with self._write_lock:
            Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()
