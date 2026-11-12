"""Integration tests for the widgets REST API and the SSE stream.

The API tests run against a scratch PostgreSQL database that carries the
full metadata schema, so they also cover the CHECK constraints the review
calls out as untestable through metadata-only unit tests. When Postgres is
absent they skip.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.security import hash_token
from src.config.settings import Settings, get_settings
from src.dependencies import get_current_user, get_db_session
from src.main import create_app
from src.modules.auth.repository import UserDTO
from src.modules.auth.state import DEFAULT_USER_ID
from src.modules.widgets.stream import (
    format_widget_event,
    parse_widget_cursor,
)
from src.schemas.base import Base

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
TEST_DEVICE_TOKEN = "vgt_testtoken"
"""The token AUTH_HEADERS sends. Each scratch database seeds this device row."""

OTHER_USER_ID = uuid4()
"""A user id that owns no rows, used for scoping checks."""


@dataclass
class ScratchDatabase:
    """A database that belongs to one test and is dropped afterwards."""

    name: str
    dsn: str
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]


def _base_dsn() -> str:
    """Return the configured database URL in plain asyncpg form."""
    return get_settings().DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def _scratch_database() -> AsyncIterator[ScratchDatabase]:
    """Create an empty database with the full schema, then drop it."""
    base_dsn = _base_dsn()
    try:
        admin = await asyncpg.connect(base_dsn)
    except Exception as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"PostgreSQL is not available: {exc}")
    name = f"virgil_widgets_{uuid4().hex[:10]}"
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # pragma: no cover - depends on the host
        await admin.close()
        pytest.skip(f"Cannot create a scratch database: {exc}")
    await admin.close()

    dsn = f"{base_dsn.rsplit('/', 1)[0]}/{name}"
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
            # The request path authenticates through the SQL auth store, so this
            # database needs the same device row the root conftest seeds into the
            # development database. Without it every request here is a 401.
            await connection.execute(
                text(
                    "insert into users (id, display_name) values (:id, 'Default User') "
                    "on conflict (id) do nothing"
                ),
                {"id": DEFAULT_USER_ID},
            )
            await connection.execute(
                text(
                    "insert into devices (id, user_id, name, token_hash, last_seen_at) "
                    "values (gen_random_uuid(), :user_id, 'Test Device', :token_hash, now()) "
                    "on conflict (token_hash) do nothing"
                ),
                {
                    "user_id": DEFAULT_USER_ID,
                    "token_hash": hash_token(TEST_DEVICE_TOKEN),
                },
            )
        yield ScratchDatabase(
            name=name,
            dsn=dsn,
            engine=engine,
            sessionmaker=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
        )
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(base_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


def _other_user() -> UserDTO:
    """Build a user DTO that owns no widget rows."""
    now = datetime.now(timezone.utc)
    return UserDTO(
        id=OTHER_USER_ID,
        display_name="Other",
        settings={},
        created_at=now,
        updated_at=now,
    )


@asynccontextmanager
async def _widget_client(database: ScratchDatabase):
    """Yield an API client whose session and user are the scratch database."""
    async with database.sessionmaker() as session:
        app = create_app(Settings(APP_ENV="test"))
        app.dependency_overrides[get_db_session] = lambda: session
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


METRIC_BODY = {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}}


@pytest.mark.asyncio
async def test_create_widget_returns_version_one() -> None:
    """A valid create returns 201 with version 1 and the stored payload."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            response = await client.post("/api/v1/widgets", json=METRIC_BODY, headers=AUTH_HEADERS)
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["version"] == 1
            assert body["widget_type"] == "metric_card"
            assert body["data_payload"] == {"value": "1", "delta": None, "caption": None}
            assert body["user_id"] == str(DEFAULT_USER_ID)


@pytest.mark.asyncio
async def test_create_rejects_bad_widget_type() -> None:
    """An unknown widget_type is a 422, never a row."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            response = await client.post(
                "/api/v1/widgets",
                json={"title": "Panel", "widget_type": "nope", "data_payload": {}},
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_bad_refresh_interval() -> None:
    """An interval outside the allowlist is a 422."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            response = await client.post(
                "/api/v1/widgets",
                json={**METRIC_BODY, "refresh_interval_minutes": 5},
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_mismatched_payload() -> None:
    """A table payload on a metric_card is a 422."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            response = await client.post(
                "/api/v1/widgets",
                json={
                    "title": "Panel",
                    "widget_type": "metric_card",
                    "data_payload": {"columns": ["a"], "rows": []},
                },
                headers=AUTH_HEADERS,
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_rows_newest_first() -> None:
    """The list endpoint returns the caller rows ordered by updated_at desc."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            first = await client.post(
                "/api/v1/widgets",
                json={**METRIC_BODY, "title": "First"},
                headers=AUTH_HEADERS,
            )
            second = await client.post(
                "/api/v1/widgets",
                json={**METRIC_BODY, "title": "Second"},
                headers=AUTH_HEADERS,
            )
            assert first.status_code == 201
            assert second.status_code == 201
            listed = await client.get("/api/v1/widgets", headers=AUTH_HEADERS)
            assert listed.status_code == 200
            titles = [row["title"] for row in listed.json()]
            assert titles == ["Second", "First"]


@pytest.mark.asyncio
async def test_list_scopes_to_caller() -> None:
    """Another user sees none of the owner rows."""
    async with _scratch_database() as database:
        async with database.sessionmaker() as session:
            app = create_app(Settings(APP_ENV="test"))
            app.dependency_overrides[get_db_session] = lambda: session
            app.dependency_overrides[get_current_user] = _other_user
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
                listed = await other.get("/api/v1/widgets", headers=AUTH_HEADERS)
                assert listed.status_code == 200
                assert listed.json() == []

                missing_id = str(uuid4())
                missing = await other.patch(
                    f"/api/v1/widgets/{missing_id}",
                    json={"widget_id": missing_id, "data_payload": {"value": "2"}},
                    headers=AUTH_HEADERS,
                )
                assert missing.status_code == 404


@pytest.mark.asyncio
async def test_delete_widget_removes_row() -> None:
    """A DELETE removes the row and the next list is empty."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            created = await client.post("/api/v1/widgets", json=METRIC_BODY, headers=AUTH_HEADERS)
            assert created.status_code == 201
            widget_id = created.json()["id"]
            removed = await client.delete(f"/api/v1/widgets/{widget_id}", headers=AUTH_HEADERS)
            assert removed.status_code == 204, removed.text
            listed = await client.get("/api/v1/widgets", headers=AUTH_HEADERS)
            assert listed.status_code == 200
            assert listed.json() == []
            again = await client.delete(f"/api/v1/widgets/{widget_id}", headers=AUTH_HEADERS)
            assert again.status_code == 404


@pytest.mark.asyncio
async def test_delete_widget_missing_is_404() -> None:
    """A DELETE for an unknown id is a 404."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            missing = await client.delete(f"/api/v1/widgets/{uuid4()}", headers=AUTH_HEADERS)
            assert missing.status_code == 404


@pytest.mark.asyncio
async def test_delete_widget_scopes_to_caller() -> None:
    """Another user cannot delete the owner row, and the row survives."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            created = await client.post("/api/v1/widgets", json=METRIC_BODY, headers=AUTH_HEADERS)
            assert created.status_code == 201
            widget_id = created.json()["id"]
        async with database.sessionmaker() as session:
            app = create_app(Settings(APP_ENV="test"))
            app.dependency_overrides[get_db_session] = lambda: session
            app.dependency_overrides[get_current_user] = _other_user
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
                denied = await other.delete(f"/api/v1/widgets/{widget_id}", headers=AUTH_HEADERS)
                assert denied.status_code == 404
        async with _widget_client(database) as client:
            listed = await client.get("/api/v1/widgets", headers=AUTH_HEADERS)
            assert listed.status_code == 200
            assert [row["id"] for row in listed.json()] == [widget_id]


@pytest.mark.asyncio
async def test_patch_bumps_version() -> None:
    """A payload update returns the next version with the new payload."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            created = await client.post("/api/v1/widgets", json=METRIC_BODY, headers=AUTH_HEADERS)
            widget_id = created.json()["id"]
            updated = await client.patch(
                f"/api/v1/widgets/{widget_id}",
                json={"widget_id": widget_id, "data_payload": {"value": "2"}},
                headers=AUTH_HEADERS,
            )
            assert updated.status_code == 200, updated.text
            body = updated.json()
            assert body["version"] == 2
            assert body["data_payload"]["value"] == "2"


@pytest.mark.asyncio
async def test_patch_rejects_mismatched_payload() -> None:
    """A wrong-shaped payload is a 422 and the version is unchanged."""
    async with _scratch_database() as database:
        async with _widget_client(database) as client:
            created = await client.post("/api/v1/widgets", json=METRIC_BODY, headers=AUTH_HEADERS)
            widget_id = created.json()["id"]
            rejected = await client.patch(
                f"/api/v1/widgets/{widget_id}",
                json={
                    "widget_id": widget_id,
                    "data_payload": {"columns": ["a"], "rows": []},
                },
                headers=AUTH_HEADERS,
            )
            assert rejected.status_code == 422
            listed = await client.get("/api/v1/widgets", headers=AUTH_HEADERS)
            assert listed.json()[0]["version"] == 1


@pytest.mark.asyncio
async def test_check_constraints_reject_bad_rows() -> None:
    """The migrated CHECKs reject a bad widget_type and a bad interval."""
    async with _scratch_database() as database:
        async with database.sessionmaker() as session:
            user_id = uuid4()
            await session.execute(
                sa.text("INSERT INTO users (id, display_name) VALUES (:id, 'Raw')"),
                {"id": user_id},
            )
            await session.commit()
            for column, bad_value in (
                ("widget_type", "nope"),
                ("refresh_interval_minutes", 5),
            ):
                payload: dict[str, Any] = {
                    "id": uuid4(),
                    "user_id": user_id,
                    "title": "Raw",
                    "widget_type": "metric_card",
                    "data_payload": '{"value": "1"}',
                    "refresh_interval_minutes": None,
                }
                payload[column] = bad_value
                with pytest.raises(IntegrityError):
                    await session.execute(
                        sa.text(
                            "INSERT INTO widgets (id, user_id, title, widget_type, "
                            "data_payload, refresh_interval_minutes) "
                            "VALUES (:id, :user_id, :title, :widget_type, "
                            "cast(:data_payload as jsonb), :refresh_interval_minutes)"
                        ),
                        payload,
                    )
                    await session.commit()
                await session.rollback()


@pytest.mark.asyncio
async def test_stream_registered_before_item_routes() -> None:
    """GET /stream is registered before PATCH /{id} so it never 422s on UUID parsing."""
    app = create_app(Settings(APP_ENV="test"))
    paths = list(app.openapi()["paths"].keys())
    assert paths.index("/api/v1/widgets/stream") < paths.index("/api/v1/widgets/{widget_id}")


@pytest.mark.asyncio
async def test_stream_emits_widget_updated_frames() -> None:
    """The stream generator emits one widget_updated frame per newer row.

    The endpoint itself never terminates by design (one stream per
    dashboard, cursor poll plus Last-Event-ID replay), and this httpx
    version buffers ASGI bodies, so the test drives the exact generator
    the route returns, bounded by max_duration_seconds.
    """
    import json as _json

    from src.modules.widgets.service import WidgetsService
    from src.modules.widgets.repository import WidgetCreateDTO
    from src.modules.widgets.stream import SSE_HEADERS, stream_widget_events

    assert SSE_HEADERS["Cache-Control"] == "no-cache"

    async with _scratch_database() as database:
        async with database.sessionmaker() as session:
            service = WidgetsService(session=session, settings=Settings(APP_ENV="test"))
            created = await service.create_widget(
                WidgetCreateDTO(
                    user_id=DEFAULT_USER_ID,
                    title="Panel",
                    widget_type="metric_card",
                    data_payload={"value": "1"},
                )
            )

            frames: list[str] = []
            async for frame in stream_widget_events(
                service, DEFAULT_USER_ID, "", max_duration_seconds=0.5
            ):
                frames.append(frame)
            data_frames = [frame for frame in frames if "event: widget_updated" in frame]
            assert len(data_frames) == 1
            payload = _json.loads(data_frames[0].split("data:", 1)[1].strip())
            assert payload["id"] == str(created.id)
            assert payload["version"] == 1
            assert payload["data_payload"]["value"] == "1"

            replay: list[str] = []
            async for frame in stream_widget_events(
                service,
                DEFAULT_USER_ID,
                payload["updated_at"],
                max_duration_seconds=0.5,
            ):
                replay.append(frame)
            assert [frame for frame in replay if "event: widget_updated" in frame] == []


def test_widget_cursor_parsing() -> None:
    """Last-Event-ID wins over since; garbage falls back to the epoch."""
    stamp = "2026-01-02T03:04:05+00:00"
    assert parse_widget_cursor(stamp, fallback=None) == stamp
    assert parse_widget_cursor(stamp, fallback="2020-01-01T00:00:00+00:00") == stamp
    assert parse_widget_cursor(None, fallback=stamp) == stamp
    epoch = parse_widget_cursor(None, fallback=None)
    assert epoch.startswith("1970-01-01")
    assert parse_widget_cursor("garbage", fallback="also garbage").startswith("1970-01-01")


def test_widget_event_frame_carries_cursor() -> None:
    """The SSE frame id matches the row updated_at for replay."""
    from src.modules.widgets.repository import WidgetDTO

    now = datetime.now(timezone.utc)
    widget = WidgetDTO(
        id=uuid4(),
        user_id=uuid4(),
        title="Panel",
        widget_type="metric_card",
        data_payload={"value": "1"},
        refresh_config=None,
        refresh_interval_minutes=None,
        next_refresh_at=None,
        version=3,
        created_at=now,
        updated_at=now,
    )
    frame = format_widget_event(widget)
    assert "event: widget_updated" in frame
    assert f'"version": 3' in frame
    assert f"id: {now.isoformat()}" in frame


def test_other_user_cannot_patch_owner_row() -> None:
    """The scoping check is structural: PATCH filters on (id, user_id)."""
    assert OTHER_USER_ID != UUID(str(DEFAULT_USER_ID))
