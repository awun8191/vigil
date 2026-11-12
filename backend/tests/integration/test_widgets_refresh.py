"""Integration tests for the due-widget refresh sweeper.

The sweeper runs over the in-memory stores or a scratch Postgres database,
so a shared development database never affects a result. When Postgres is
absent the Postgres case skips.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.modules.jobs.service import JobsService
from src.modules.widgets.repository import WidgetCreateDTO
from src.modules.widgets.service import WidgetsService
from src.schemas import Base
from src.worker import sweep_due_widget_refreshes


@dataclass
class RefreshHarness:
    """Widget and job services over one storage backend."""

    label: str
    widgets: WidgetsService
    jobs: JobsService
    session: Any
    engine: Any | None = None
    owner_id: Any | None = None

    async def pending_refresh_jobs(self) -> list[dict[str, Any]]:
        """Return the payload of every pending refresh_widget job."""
        if self.engine is not None:
            from sqlalchemy import select

            from src.schemas.system import JobModel

            assert self.session is not None
            maker = async_sessionmaker(
                bind=self.engine, class_=AsyncSession, expire_on_commit=False
            )
            async with maker() as fresh:
                rows = (
                    await fresh.execute(
                        select(JobModel).where(
                            JobModel.type == "refresh_widget",
                            JobModel.status == "pending",
                        )
                    )
                ).scalars().all()
                return [dict(row.payload) for row in rows]
        return [
            dict(job.payload)
            for job in JobsService._jobs.values()
            if job.type == "refresh_widget" and job.status == "pending"
        ]


def _clear_stores() -> None:
    """Empty the shared in-memory job store so tests never share rows."""
    JobsService._jobs.clear()


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def harness(request: pytest.FixtureRequest) -> AsyncIterator[RefreshHarness]:
    """Yield the widget stack over the in-memory store or a scratch Postgres."""
    settings = Settings(APP_ENV="test")
    _clear_stores()

    if request.param == "memory":
        store: dict[Any, Any] = {}
        stub = SimpleNamespace(_widgets_memory=store)
        jobs = JobsService(session=stub, settings=settings)  # type: ignore[arg-type]
        widgets = WidgetsService(session=stub, settings=settings)  # type: ignore[arg-type]
        yield RefreshHarness("memory", widgets, jobs, stub)
        return

    base_url, _, _ = settings.DATABASE_URL.rpartition("/")
    admin_url = f"{base_url}/postgres"
    db_name = f"v07_widgets_refresh_{uuid4().hex[:10]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await admin.dispose()
        pytest.skip(f"PostgreSQL test database is not available: {exc}")

    async with admin.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    engine = create_async_engine(f"{base_url}/{db_name}")
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        session = maker()
        try:
            owner_id = uuid4()
            await session.execute(
                sa.text("INSERT INTO users (id, display_name) VALUES (:id, 'Widget Owner')"),
                {"id": owner_id},
            )
            await session.commit()
            jobs = JobsService(session=session, settings=settings)
            widgets = WidgetsService(session=session, settings=settings)
            yield RefreshHarness("postgres", widgets, jobs, session, engine, owner_id)
        finally:
            await session.close()
    finally:
        await engine.dispose()
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        except Exception:  # pragma: no cover - environment dependent
            pass
        await admin.dispose()


async def _create_widget(
    harness: RefreshHarness,
    title: str,
    next_refresh_at: datetime | None,
    widget_type: str = "metric_card",
) -> Any:
    """Insert one widget row on the harness backend."""
    owner = harness.owner_id if harness.owner_id is not None else uuid4()
    return await harness.widgets.create_widget(
        WidgetCreateDTO(
            user_id=owner,
            title=title,
            widget_type=widget_type,
            data_payload={"value": "1"},
            refresh_config={"kind": "static"},
            refresh_interval_minutes=60,
            next_refresh_at=next_refresh_at,
        )
    )


@pytest.mark.asyncio
async def test_due_row_gets_one_refresh_job(harness: RefreshHarness) -> None:
    """A due row produces exactly one pending refresh_widget job."""
    created = await _create_widget(
        harness, "Due", datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    enqueued = await sweep_due_widget_refreshes(harness.session, harness.widgets.settings)
    assert enqueued == 1
    pending = await harness.pending_refresh_jobs()
    assert len(pending) == 1
    assert pending[0]["widget_id"] == str(created.id)


@pytest.mark.asyncio
async def test_future_row_gets_no_job(harness: RefreshHarness) -> None:
    """A row whose refresh is in the future is left alone."""
    await _create_widget(harness, "Future", datetime.now(timezone.utc) + timedelta(hours=2))
    enqueued = await sweep_due_widget_refreshes(harness.session, harness.widgets.settings)
    assert enqueued == 0
    assert await harness.pending_refresh_jobs() == []


@pytest.mark.asyncio
async def test_sweeper_rerun_does_not_duplicate(harness: RefreshHarness) -> None:
    """A second sweep skips rows that already have a pending refresh job."""
    await _create_widget(harness, "Due", datetime.now(timezone.utc) - timedelta(minutes=5))
    first = await sweep_due_widget_refreshes(harness.session, harness.widgets.settings)
    second = await sweep_due_widget_refreshes(harness.session, harness.widgets.settings)
    assert first == 1
    assert second == 0
    assert len(await harness.pending_refresh_jobs()) == 1


@pytest.mark.asyncio
async def test_sweeper_enqueues_each_due_row_once(harness: RefreshHarness) -> None:
    """Three due rows produce three jobs, one per widget."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _create_widget(harness, "Due one", past)
    await _create_widget(harness, "Due two", past)
    await _create_widget(harness, "Due three", past)
    await _create_widget(harness, "Future", datetime.now(timezone.utc) + timedelta(hours=2))
    enqueued = await sweep_due_widget_refreshes(harness.session, harness.widgets.settings)
    assert enqueued == 3
    pending = await harness.pending_refresh_jobs()
    assert len(pending) == 3
    assert len({item["widget_id"] for item in pending}) == 3
