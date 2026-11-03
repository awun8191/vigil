"""GET /runs/configs/default must not rewrite the row it returns.

The route used to rewrite ``vigil-default`` on every call, including the
``approval_policy`` (reset to ``local`` for every tool) and the ``system_prompt``.
A GET has no write side effect: the row is inserted once, and a policy a client
saved survives the next load.
"""
from __future__ import annotations

from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.dependencies import get_runs_service
from src.main import create_app
from src.modules.runs.repository import AgentConfigDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
DEFAULT_CONFIG_NAME = "vigil-default"


class _CountingJournal(InMemoryRunJournal):
    """An in-memory journal that records every config write."""

    def __init__(self) -> None:
        super().__init__()
        self.saved_configs: list[AgentConfigDTO] = []

    async def save_config(self, config: AgentConfigDTO) -> AgentConfigDTO:
        self.saved_configs.append(config)
        return await super().save_config(config)


@pytest.fixture
def app():
    """A fresh app per test, so dependency overrides do not leak."""
    return create_app(Settings(DEBUG=True))


@pytest.mark.asyncio
async def test_default_config_get_does_not_rewrite_the_row(app) -> None:
    """Calling the GET twice writes once and keeps the policy the client saved."""
    journal = _CountingJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        journal=journal,  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_runs_service] = lambda: service

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/runs/configs/default", headers=AUTH_HEADERS)
        assert first.status_code == 200
        created = await service.get_agent_config_by_name(DEFAULT_CONFIG_NAME)
        assert created is not None
        original_id = created.id
        original_policy = dict(created.approval_policy)
        assert len(journal.saved_configs) == 1

        second = await client.get("/api/v1/runs/configs/default", headers=AUTH_HEADERS)
        assert second.status_code == 200
        assert second.json()["id"] == str(original_id)
        assert len(journal.saved_configs) == 1, "a GET must not write the config row"
        still = await service.get_agent_config_by_name(DEFAULT_CONFIG_NAME)
        assert still is not None
        assert still.id == original_id
        assert still.approval_policy == original_policy

        # A client edits the policy and the prompt, then reloads the app.
        edited = AgentConfigDTO(
            id=original_id,
            name=DEFAULT_CONFIG_NAME,
            system_prompt="A prompt the owner wrote.",
            model=created.model,
            tools=list(created.tools),
            skills=list(created.skills),
            memory_scopes=list(created.memory_scopes),
            approval_policy={"ask_user": "external", "run_shell": "background"},
            limits=dict(created.limits),
        )
        await service.save_agent_config(edited)
        writes_before = len(journal.saved_configs)

        third = await client.get("/api/v1/runs/configs/default", headers=AUTH_HEADERS)
        assert third.status_code == 200
        assert third.json()["id"] == str(original_id)
        assert len(journal.saved_configs) == writes_before

        kept = await service.get_agent_config_by_name(DEFAULT_CONFIG_NAME)
        assert kept is not None
        assert kept.approval_policy == {"ask_user": "external", "run_shell": "background"}
        assert kept.system_prompt == "A prompt the owner wrote."
