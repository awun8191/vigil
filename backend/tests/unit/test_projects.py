"""Unit and HTTP tests for scoped Project Workspaces and run project_id scoping."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.dependencies import (
    get_current_device,
    get_current_user,
    get_projects_service,
    get_runs_service,
)
from src.main import create_app
from src.modules.auth.repository import DeviceDTO, UserDTO
from src.modules.projects.repository import (
    ProjectCreateDTO,
    ProjectsRepository,
    ProjectUpdateDTO,
)
from src.modules.projects.service import ProjectsService
from src.modules.runs.core_context import compose_run_system_prompt
from src.modules.runs.service import RunsService


def test_projects_service_satisfies_protocol(tmp_path: Path) -> None:
    settings = Settings(WORKSPACE_BASE_PATH=str(tmp_path))
    service = ProjectsService(session=None, settings=settings)
    assert isinstance(service, ProjectsRepository)


@pytest.mark.asyncio
async def test_project_crud_slug_collision_and_workspace_files(tmp_path: Path) -> None:
    settings = Settings(WORKSPACE_BASE_PATH=str(tmp_path))
    service = ProjectsService(session=None, settings=settings)
    user_id = uuid4()

    first = await service.create_project(
        ProjectCreateDTO(
            user_id=user_id,
            name="Vigil Web Redesign",
            description="Hark Pro canvas",
            instructions="Keep components modular.",
        )
    )
    second = await service.create_project(
        ProjectCreateDTO(user_id=user_id, name="Vigil Web Redesign")
    )

    assert first.slug == "vigil-web-redesign"
    assert second.slug == "vigil-web-redesign-2"
    assert Path(first.workspace_path).is_dir()
    assert Path(second.workspace_path).is_dir()

    # Create a normal file, a hidden file, and inspect listing
    ws = Path(first.workspace_path)
    (ws / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (ws / ".secret").write_text("ignored", encoding="utf-8")

    files = service.list_workspace_files(first)
    assert [f.path for f in files] == ["notes.md", "src/main.py"]

    updated = await service.update_project(
        first.id,
        ProjectUpdateDTO(description="Updated desc", instructions="Always write tests."),
    )
    assert updated is not None
    assert updated.description == "Updated desc"
    assert updated.instructions == "Always write tests."

    listed = await service.list_projects(user_id)
    assert len(listed) == 2
    assert await service.delete_project(second.id) is True
    assert len(await service.list_projects(user_id)) == 1


@pytest.mark.asyncio
async def test_projects_http_endpoints_and_ownership(tmp_path: Path) -> None:
    settings = Settings(APP_ENV="test", WORKSPACE_BASE_PATH=str(tmp_path))
    app = create_app(settings)

    now = datetime.now(timezone.utc)
    owner = UserDTO(
        id=uuid4(),
        display_name="Owner",
        settings={},
        created_at=now,
        updated_at=now,
    )
    other = UserDTO(
        id=uuid4(),
        display_name="Other",
        settings={},
        created_at=now,
        updated_at=now,
    )
    shared_service = ProjectsService(session=None, settings=settings)
    runs_service = RunsService(session=None, settings=settings)

    active_user = owner
    app.dependency_overrides[get_projects_service] = lambda: shared_service
    app.dependency_overrides[get_runs_service] = lambda: runs_service
    app.dependency_overrides[get_current_user] = lambda: active_user
    app.dependency_overrides[get_current_device] = lambda: DeviceDTO(
        id=uuid4(),
        user_id=active_user.id,
        name="Test Device",
        token_hash="hash",
        push_token=None,
        revoked_at=None,
        last_seen_at=now,
        created_at=now,
    )

    headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/projects",
            json={
                "name": "Alpha Workspace",
                "description": "Core repo",
                "instructions": "Prefer uv run pytest.",
            },
            headers=headers,
        )
        assert created.status_code == 201
        body = created.json()
        project_id = body["id"]
        assert body["slug"] == "alpha-workspace"

        Path(body["workspace_path"], "README.md").write_text("hi", encoding="utf-8")

        detail = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
        assert detail.status_code == 200
        assert [f["path"] for f in detail.json()["files"]] == ["README.md"]

        listed = await client.get("/api/v1/projects", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        patched = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Alpha v2"},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Alpha v2"

        # Another user cannot read this project or attach a run to it
        active_user = other
        forbidden_get = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
        assert forbidden_get.status_code == 404

        cfg = await runs_service.get_or_create_default_agent_config()
        forbidden_run = await client.post(
            "/api/v1/runs",
            json={
                "config_id": str(cfg.id),
                "initial_prompt": "Hijack project",
                "project_id": project_id,
            },
            headers=headers,
        )
        assert forbidden_run.status_code == 404

        active_user = owner
        allowed_run = await client.post(
            "/api/v1/runs",
            json={
                "config_id": str(cfg.id),
                "initial_prompt": "Work in project",
                "project_id": project_id,
            },
            headers=headers,
        )
        assert allowed_run.status_code == 201
        assert allowed_run.json()["project_id"] == project_id

        # Upload a file directly to the project workspace
        upload_proj_res = await client.post(
            f"/api/v1/projects/{project_id}/files",
            files=[("files", ("spec.md", b"# Spec\nDetails here", "text/markdown"))],
            headers=headers,
        )
        assert upload_proj_res.status_code == 201
        proj_files = upload_proj_res.json()
        assert any(f["path"] == "spec.md" for f in proj_files)

        # Upload a file for a chat run via POST /api/v1/runs/uploads and attach it to POST /api/v1/runs
        upload_run_res = await client.post(
            "/api/v1/runs/uploads",
            data={"project_id": project_id},
            files=[("files", ("data.csv", b"col1,col2\n10,20\n", "text/csv"))],
            headers=headers,
        )
        assert upload_run_res.status_code == 201
        uploaded_files = upload_run_res.json()["files"]
        assert len(uploaded_files) == 1
        assert uploaded_files[0]["name"] == "data.csv"
        assert uploaded_files[0]["workspace_path"] == "/workspace/data.csv"

        run_with_att = await client.post(
            "/api/v1/runs",
            json={
                "config_id": str(cfg.id),
                "initial_prompt": "",
                "project_id": project_id,
                "attachments": uploaded_files,
            },
            headers=headers,
        )
        assert run_with_att.status_code == 201
        att_run_id = run_with_att.json()["id"]
        msgs_res = await client.get(f"/api/v1/runs/{att_run_id}/messages", headers=headers)
        assert msgs_res.status_code == 200
        msgs = msgs_res.json()
        assert len(msgs) == 1
        assert "[Attached Files in /workspace]" in msgs[0]["content"]
        assert "/workspace/data.csv" in msgs[0]["content"]

        deleted = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
        assert deleted.status_code == 204
        assert (await client.get(f"/api/v1/projects/{project_id}", headers=headers)).status_code == 404


def test_compose_run_system_prompt_injects_project_workspace() -> None:
    prompt = compose_run_system_prompt(
        None,
        project_context={
            "name": "Vigil Core",
            "slug": "vigil-core",
            "workspace_path": "/var/vigil/workspaces/projects/u/vigil-core",
            "description": "Backend refactor",
            "instructions": "Run pytest after every edit.",
        },
    )
    assert prompt is not None
    assert "## Active Project Workspace" in prompt
    assert "Vigil Core" in prompt
    assert "vigil-core" in prompt
    assert "Run pytest after every edit." in prompt
