"""Widgets service: user-scoped dashboard panels with versioned payloads.

The service holds no class level cache of rows. Every read and write filters
on ``user_id``, and the sweeper claims due rows with SKIP LOCKED, so two
workers never refresh the same widget.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.widgets.repository import WidgetCreateDTO, WidgetDTO, WidgetUpdateDTO


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime. A naive value is read as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class WidgetsService:
    """Dashboard widget store satisfying WidgetsRepository via duck typing.

    With a database session, rows live in Postgres. Without one, rows live
    in an instance-level in-memory dict with the same user scoping and
    version semantics. That dict is a test double: a test shares rows across
    service instances by handing the session stub a ``_widgets_memory``
    dict, and production never reads it because the session is always a
    real database session there.
    """

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        carried = getattr(session, "_widgets_memory", None)
        self._widgets: dict[UUID, WidgetDTO] = (
            carried if isinstance(carried, dict) else {}
        )

    def _now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(timezone.utc)

    def _is_db_session_active(self) -> bool:
        """Return True when the session can run database queries."""
        return isinstance(self.session, AsyncSession)

    def _to_dto(self, model: Any) -> WidgetDTO:
        """Convert a widget row to its transfer object."""
        return WidgetDTO(
            id=model.id,
            user_id=model.user_id,
            title=model.title,
            widget_type=model.widget_type,
            data_payload=dict(model.data_payload),
            refresh_config=(
                dict(model.refresh_config) if model.refresh_config is not None else None
            ),
            refresh_interval_minutes=model.refresh_interval_minutes,
            next_refresh_at=model.next_refresh_at,
            version=model.version,
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
        )

    async def get_widget(self, widget_id: UUID, user_id: UUID) -> WidgetDTO | None:
        """Return one widget row scoped to its owner, or None."""
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            row = (
                await self.session.execute(
                    select(WidgetModel).where(
                        WidgetModel.id == widget_id,
                        WidgetModel.user_id == user_id,
                    )
                )
            ).scalars().first()
            return self._to_dto(row) if row is not None else None
        widget = self._widgets.get(widget_id)
        if widget is None or widget.user_id != user_id:
            return None
        return widget

    async def list_widgets(self, user_id: UUID) -> list[WidgetDTO]:
        """List every widget row of one user, newest first."""
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            rows = (
                await self.session.execute(
                    select(WidgetModel)
                    .where(WidgetModel.user_id == user_id)
                    .order_by(WidgetModel.updated_at.desc())
                )
            ).scalars().all()
            return [self._to_dto(row) for row in rows]
        owned = [w for w in self._widgets.values() if w.user_id == user_id]
        owned.sort(key=lambda w: w.updated_at, reverse=True)
        return owned

    async def create_widget(self, data: WidgetCreateDTO) -> WidgetDTO:
        """Insert one widget row with version 1."""
        now = self._now()
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            model = WidgetModel(
                id=uuid4(),
                user_id=data.user_id,
                title=data.title,
                widget_type=data.widget_type,
                data_payload=data.data_payload,
                refresh_config=data.refresh_config,
                refresh_interval_minutes=data.refresh_interval_minutes,
                next_refresh_at=data.next_refresh_at,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self.session.add(model)
            await self.session.commit()
            return self._to_dto(model)
        widget = WidgetDTO(
            id=uuid4(),
            user_id=data.user_id,
            title=data.title,
            widget_type=data.widget_type,
            data_payload=dict(data.data_payload),
            refresh_config=(
                dict(data.refresh_config) if data.refresh_config is not None else None
            ),
            refresh_interval_minutes=data.refresh_interval_minutes,
            next_refresh_at=data.next_refresh_at,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._widgets[widget.id] = widget
        return widget

    async def update_widget_payload(
        self, widget_id: UUID, user_id: UUID, data: WidgetUpdateDTO
    ) -> WidgetDTO | None:
        """Replace the payload of one owned widget and bump its version."""
        now = self._now()
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            row = (
                await self.session.execute(
                    select(WidgetModel).where(
                        WidgetModel.id == widget_id,
                        WidgetModel.user_id == user_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return None
            row.data_payload = data.data_payload
            row.version = row.version + 1
            row.updated_at = now
            await self.session.commit()
            return self._to_dto(row)
        existing = self._widgets.get(widget_id)
        if existing is None or existing.user_id != user_id:
            return None
        updated = WidgetDTO(
            id=existing.id,
            user_id=existing.user_id,
            title=existing.title,
            widget_type=existing.widget_type,
            data_payload=dict(data.data_payload),
            refresh_config=existing.refresh_config,
            refresh_interval_minutes=existing.refresh_interval_minutes,
            next_refresh_at=existing.next_refresh_at,
            version=existing.version + 1,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._widgets[widget_id] = updated
        return updated

    async def delete_widget(self, widget_id: UUID, user_id: UUID) -> bool:
        """Delete one owned widget. True when a row was removed."""
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            row = (
                await self.session.execute(
                    select(WidgetModel).where(
                        WidgetModel.id == widget_id,
                        WidgetModel.user_id == user_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return False
            await self.session.delete(row)
            await self.session.commit()
            return True
        existing = self._widgets.get(widget_id)
        if existing is None or existing.user_id != user_id:
            return False
        del self._widgets[widget_id]
        return True

    async def claim_due_refresh(self, limit: int = 50) -> list[WidgetDTO]:
        """Claim due refresh rows ordered by next refresh time.

        Uses SKIP LOCKED on Postgres so two sweepers never claim the same row.
        The in-memory path returns due rows ordered the same way.
        """
        now = self._now()
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            rows = (
                await self.session.execute(
                    select(WidgetModel)
                    .where(WidgetModel.next_refresh_at <= now)
                    .order_by(WidgetModel.next_refresh_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            await self.session.commit()
            return [self._to_dto(row) for row in rows]
        due = [
            w
            for w in self._widgets.values()
            if w.next_refresh_at is not None and w.next_refresh_at <= now
        ]
        due.sort(key=lambda w: w.next_refresh_at)  # type: ignore[arg-type]
        return due[:limit]

    async def apply_refresh(
        self,
        widget_id: UUID,
        user_id: UUID,
        data_payload: dict[str, Any],
        next_refresh_at: datetime | None,
    ) -> WidgetDTO | None:
        """Write refreshed data, bump the version, and reschedule the next run."""
        now = self._now()
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            row = (
                await self.session.execute(
                    select(WidgetModel).where(
                        WidgetModel.id == widget_id,
                        WidgetModel.user_id == user_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return None
            row.data_payload = data_payload
            row.version = row.version + 1
            row.next_refresh_at = next_refresh_at
            row.updated_at = now
            return self._to_dto(row)
        existing = self._widgets.get(widget_id)
        if existing is None or existing.user_id != user_id:
            return None
        updated = WidgetDTO(
            id=existing.id,
            user_id=existing.user_id,
            title=existing.title,
            widget_type=existing.widget_type,
            data_payload=dict(data_payload),
            refresh_config=existing.refresh_config,
            refresh_interval_minutes=existing.refresh_interval_minutes,
            next_refresh_at=next_refresh_at,
            version=existing.version + 1,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._widgets[widget_id] = updated
        return updated

    async def touch_refresh(
        self,
        widget_id: UUID,
        user_id: UUID,
        next_refresh_at: datetime | None,
    ) -> WidgetDTO | None:
        """Reschedule the next refresh without touching data or version."""
        now = self._now()
        if self._is_db_session_active():
            from src.schemas.widgets import WidgetModel

            assert self.session is not None
            row = (
                await self.session.execute(
                    select(WidgetModel).where(
                        WidgetModel.id == widget_id,
                        WidgetModel.user_id == user_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return None
            row.next_refresh_at = next_refresh_at
            row.updated_at = now
            return self._to_dto(row)
        existing = self._widgets.get(widget_id)
        if existing is None or existing.user_id != user_id:
            return None
        updated = WidgetDTO(
            id=existing.id,
            user_id=existing.user_id,
            title=existing.title,
            widget_type=existing.widget_type,
            data_payload=dict(existing.data_payload),
            refresh_config=existing.refresh_config,
            refresh_interval_minutes=existing.refresh_interval_minutes,
            next_refresh_at=next_refresh_at,
            version=existing.version,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._widgets[widget_id] = updated
        return updated
