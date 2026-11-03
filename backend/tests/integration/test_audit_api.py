"""Integration tests for the append-only audit log.

The log records one row per privileged action through ``GET /api/v1/audit``.
The write path is append only: no route creates, updates, or deletes a row,
and the ``details`` payload never keeps a token, a password, or a credential.
"""
from __future__ import annotations

import json
from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.dependencies import get_approvals_service, get_audit_service
from src.main import create_app
from src.modules.approvals.repository import ApprovalCreateDTO
from src.modules.approvals.service import ApprovalsService
from src.modules.audit.repository import AuditEntryCreateDTO
from src.modules.audit.service import (
    ACTION_APPROVAL_DECISION,
    AuditService,
    redact_details,
)
from src.modules.jobs.service import JobsService
from src.modules.runs.repository import RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.settings.service import SettingsService

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
"""Bearer token that the test environment accepts."""


def _clear_stores() -> None:
    """Empty every in-memory store so one test never reads another test row.

    The connector service holds no process-wide account store any more: each
    service owns its repository, so there is nothing here to empty.
    """
    ApprovalsService._approvals.clear()
    JobsService._jobs.clear()
    AuditService._events.clear()
    SettingsService._user_settings.clear()


def _memory_audit(settings: Settings) -> AuditService:
    """Return one audit service over the in-memory store (no database)."""
    return AuditService(session=None, settings=settings)  # type: ignore[arg-type]


def _build_app(
    settings: Settings,
    approvals: ApprovalsService | None = None,
    audit: AuditService | None = None,
):
    """Build a test app with memory service overrides (no database)."""
    app = create_app(settings)
    if approvals is not None:
        app.dependency_overrides[get_approvals_service] = lambda: approvals
    if audit is not None:
        app.dependency_overrides[get_audit_service] = lambda: audit
    return app


@pytest.mark.asyncio
async def test_decision_is_audited() -> None:
    """An approval decision produces exactly one row with action and subject."""
    _clear_stores()
    settings = Settings(APP_ENV="test")
    audit = _memory_audit(settings)
    jobs = JobsService(session=None, settings=settings)  # type: ignore[arg-type]
    runs = RunsService(session=None, settings=settings)  # type: ignore[arg-type]
    approvals = ApprovalsService(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        jobs_service=jobs,
        runs_service=runs,
        audit=audit,
    )
    run = await runs.create_run(RunCreateDTO(config_id=uuid4(), status="awaiting_approval"))
    approval = await approvals.create_approval(
        ApprovalCreateDTO(
            run_id=run.id,
            tool_call_id="call_audit_1",
            tool_name="gmail_send",
            arguments={"to": "customer@example.com"},
        )
    )
    app = _build_app(settings, approvals, audit)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        decision = await client.post(
            f"/api/v1/approvals/{approval.id}/decision",
            json={"decision": "approved", "decision_note": "Send it"},
            headers=AUTH_HEADERS,
        )
        assert decision.status_code == 202

        page = await client.get("/api/v1/audit", headers=AUTH_HEADERS)

    assert page.status_code == 200
    body = page.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["action"] == ACTION_APPROVAL_DECISION
    assert row["subject"] == str(approval.id)
    assert row["actor"] == "user"


@pytest.mark.asyncio
async def test_credentials_redacted() -> None:
    """A details payload with a token is stored without the token."""
    _clear_stores()
    settings = Settings(APP_ENV="test")
    service = _memory_audit(settings)
    secret_token = "secret_token_value_abc123"
    stored = await service.append(
        AuditEntryCreateDTO(
            actor="user",
            action="connector_connect",
            subject="account-1",
            details={
                "refresh_token": secret_token,
                "nested": {"password": "hunter2", "label": "work"},
                "note": "Bearer secret_token_value_abc123",
            },
        )
    )

    dumped = json.dumps(stored.details)
    assert secret_token not in dumped
    assert "hunter2" not in dumped
    assert stored.details["refresh_token"] == "[REDACTED]"
    assert stored.details["nested"]["password"] == "[REDACTED]"
    assert stored.details["nested"]["label"] == "work"

    # The pure helper redacts the same way, without a write.
    assert secret_token not in json.dumps(redact_details({"api_key": secret_token}))


@pytest.mark.asyncio
async def test_audit_is_append_only() -> None:
    """No route accepts PUT, PATCH, or DELETE on an audit row."""
    _clear_stores()
    settings = Settings(APP_ENV="test")
    audit = _memory_audit(settings)
    await audit.append(
        AuditEntryCreateDTO(
            actor="user",
            action="seed.one",
            subject="subject-1",
            details={},
        )
    )
    app = _build_app(settings, audit=audit)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/audit", headers=AUTH_HEADERS)
        assert first.status_code == 200
        row_id = first.json()["items"][0]["id"]

        for method in ("put", "patch", "delete"):
            collection = await client.request(method, "/api/v1/audit", headers=AUTH_HEADERS)
            assert collection.status_code in (404, 405), (method, collection.status_code)
            row = await client.request(
                method, f"/api/v1/audit/{row_id}", json={}, headers=AUTH_HEADERS
            )
            assert row.status_code in (404, 405), (method, row.status_code)

        posted = await client.post("/api/v1/audit", json={}, headers=AUTH_HEADERS)
        assert posted.status_code in (404, 405), posted.status_code


@pytest.mark.asyncio
async def test_audit_pagination() -> None:
    """The cursor page returns newest first and honours the action filter."""
    _clear_stores()
    settings = Settings(APP_ENV="test")
    audit = _memory_audit(settings)
    for index in range(5):
        await audit.append(
            AuditEntryCreateDTO(
                actor="user",
                action=f"seed.{index % 2}",
                subject=f"subject-{index}-{uuid4().hex[:8]}",
                details={"index": index},
            )
        )
    app = _build_app(settings, audit=audit)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/audit", params={"limit": 2}, headers=AUTH_HEADERS)
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        created = [item["created_at"] for item in first_body["items"]]
        assert created == sorted(created, reverse=True)

        second = await client.get(
            "/api/v1/audit",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
            headers=AUTH_HEADERS,
        )
        assert second.status_code == 200
        second_body = second.json()
        first_ids = {item["id"] for item in first_body["items"]}
        second_ids = {item["id"] for item in second_body["items"]}
        assert first_ids.isdisjoint(second_ids)

        filtered = await client.get(
            "/api/v1/audit", params={"action": "seed.0"}, headers=AUTH_HEADERS
        )
        assert filtered.status_code == 200
        filtered_body = filtered.json()
        assert len(filtered_body["items"]) > 0
        assert all(item["action"] == "seed.0" for item in filtered_body["items"])
