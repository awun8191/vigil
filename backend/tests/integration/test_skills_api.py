"""Integration tests for the skills registry, the API routes, and the load tool.

The registry tests use a scratch PostgreSQL database, so they never depend on the
state of the development database. When PostgreSQL is absent they skip. The load
test needs no database: a recording runner stands in for the sandbox.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings, get_settings
from src.dependencies import get_db_session
from src.main import create_app
from src.modules.runs.tools import ToolContext, invoke_tool
from src.modules.skills.repository import SkillCreateDTO
from src.modules.skills.service import SkillsService
from src.schemas.base import Base

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}

SKILL_MANIFEST = """---
name: demo
description: A demonstration skill.
version: 1.0.0
---

Run the demo script.
"""


@dataclass
class ScratchDatabase:
    """A database that belongs to one test and is dropped afterwards."""

    name: str
    dsn: str
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]


def _base_dsn() -> str:
    """Return the configured database URL in plain asyncpg form."""
    return get_settings().DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )


@asynccontextmanager
async def _scratch_database() -> AsyncIterator[ScratchDatabase]:
    """Create an empty database with the full schema, then drop it."""
    base_dsn = _base_dsn()
    try:
        admin = await asyncpg.connect(base_dsn)
    except Exception as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"PostgreSQL is not available: {exc}")
    name = f"virgil_skills_{uuid4().hex[:10]}"
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # pragma: no cover - depends on the host
        await admin.close()
        pytest.skip(f"Cannot create a scratch database: {exc}")
    await admin.close()

    dsn = f"{base_dsn.rsplit('/', 1)[0]}/{name}"
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
        yield ScratchDatabase(
            name=name,
            dsn=dsn,
            engine=engine,
            sessionmaker=async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            ),
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


def _skill_dir(root: Path, name: str = "demo") -> Path:
    """Create one extracted skill directory below the root."""
    skill_dir = root / name
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MANIFEST, encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text(
        "print('demo')\n", encoding="utf-8"
    )
    return skill_dir


@pytest.mark.asyncio
async def test_skill_survives_new_service(tmp_path: Path) -> None:
    """A skill installed through one instance is visible to a new instance."""
    async with _scratch_database() as database:
        settings = Settings(APP_ENV="test", SKILLS_ROOT=str(tmp_path / "skills"))
        skill_dir = _skill_dir(tmp_path / "skills")

        async with database.sessionmaker() as first_session:
            first = SkillsService(session=first_session, settings=settings)
            installed = await first.save_skill(
                SkillCreateDTO(
                    name="demo",
                    description="A demonstration skill.",
                    version="1.0.0",
                    storage_path=str(skill_dir),
                )
            )

        # A new instance, a new session, and no shared object state.
        async with database.sessionmaker() as second_session:
            second = SkillsService(session=second_session, settings=settings)
            listed = await second.list_skills()

            assert [skill.name for skill in listed] == ["demo"]
            loaded = await second.get_by_name("demo")

        assert loaded is not None
        assert loaded.id == installed.id
        assert loaded.version == "1.0.0"


@pytest.mark.asyncio
async def test_enable_and_disable_keep_the_row(tmp_path: Path) -> None:
    """Enable and disable change the flag and never delete the row."""
    async with _scratch_database() as database:
        settings = Settings(APP_ENV="test", SKILLS_ROOT=str(tmp_path / "skills"))
        skill_dir = _skill_dir(tmp_path / "skills")

        async with database.sessionmaker() as session:
            service = SkillsService(session=session, settings=settings)
            installed = await service.save_skill(
                SkillCreateDTO(
                    name="demo",
                    description="A demonstration skill.",
                    version="1.0.0",
                    storage_path=str(skill_dir),
                )
            )

            disabled = await service.update_status(installed.id, False)
            assert disabled is not None
            assert disabled.enabled is False
            assert await service.get_by_id(installed.id) is not None
            assert await service.list_skills(enabled_only=True) == []

            enabled = await service.update_status(installed.id, True)
            assert enabled is not None
            assert enabled.enabled is True
            assert [skill.name for skill in await service.list_skills()] == ["demo"]


@pytest.mark.asyncio
async def test_skills_api_lists_and_toggles(tmp_path: Path) -> None:
    """The API lists a skill, disables it, and enables it again."""
    async with _scratch_database() as database:
        settings = Settings(APP_ENV="test", SKILLS_ROOT=str(tmp_path / "skills"))
        skill_dir = _skill_dir(tmp_path / "skills")

        async with database.sessionmaker() as session:
            service = SkillsService(session=session, settings=settings)
            installed = await service.save_skill(
                SkillCreateDTO(
                    name="demo",
                    description="A demonstration skill.",
                    version="1.0.0",
                    storage_path=str(skill_dir),
                )
            )

            app = create_app(Settings(APP_ENV="test"))
            app.dependency_overrides[get_db_session] = lambda: session
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                listed = await client.get("/api/v1/skills", headers=AUTH_HEADERS)
                assert listed.status_code == 200
                assert [item["name"] for item in listed.json()] == ["demo"]
                assert listed.json()[0]["version"] == "1.0.0"

                disabled = await client.post(
                    f"/api/v1/skills/{installed.id}/disable", headers=AUTH_HEADERS
                )
                assert disabled.status_code == 200
                assert disabled.json()["enabled"] is False

                enabled = await client.post(
                    f"/api/v1/skills/{installed.id}/enable", headers=AUTH_HEADERS
                )
                assert enabled.status_code == 200
                assert enabled.json()["enabled"] is True

                rejected = await client.post(
                    "/api/v1/skills/install",
                    json={
                        "repo_url": "https://example.invalid/demo.git",
                        "commit": "main",
                    },
                    headers=AUTH_HEADERS,
                )
                assert rejected.status_code == 422


class RecordingRunner:
    """A sandbox runner stub that records every file it writes."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, str]] = []

    async def write_file(self, container_id: str, path: str, content: bytes) -> None:
        self.calls.append(("write_file", container_id, path))
        self.files[path] = content


@pytest.mark.asyncio
async def test_skill_load_copies_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill_load writes the expected files into the sandbox workspace."""
    root = tmp_path / "skills"
    _skill_dir(root)
    settings = Settings(APP_ENV="test", SKILLS_ROOT=str(root))
    monkeypatch.setattr(
        "src.modules.runs.tools.skills.get_settings", lambda: settings
    )

    runner = RecordingRunner()
    context = ToolContext(
        runner=runner,
        container_id="run-1",
        delivery_dir=tmp_path / "delivery",
    )

    result = await invoke_tool("skill_load", {"name": "demo"}, context)

    assert result.ok is True
    assert sorted(result.content) == ["SKILL.md", "scripts/run.py"]
    assert runner.files["/workspace/skills/demo/SKILL.md"] == SKILL_MANIFEST.encode(
        "utf-8"
    )
    assert runner.files["/workspace/skills/demo/scripts/run.py"] == b"print('demo')\n"


@pytest.mark.asyncio
async def test_skill_load_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill_load refuses a name that escapes the configured root."""
    root = tmp_path / "skills"
    root.mkdir()
    settings = Settings(APP_ENV="test", SKILLS_ROOT=str(root))
    monkeypatch.setattr(
        "src.modules.runs.tools.skills.get_settings", lambda: settings
    )
    runner = RecordingRunner()
    context = ToolContext(
        runner=runner,
        container_id="run-1",
        delivery_dir=tmp_path / "delivery",
    )

    result = await invoke_tool("skill_load", {"name": "../../etc"}, context)

    assert result.ok is False
    assert runner.files == {}
