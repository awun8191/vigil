"""POST /runs must check thread ownership, like the read routes do.

``GET /runs/latest`` and ``GET /chat/sessions/{id}/messages`` already answer 404
for a session that belongs to another user. The write route used to accept any
``session_id``, so a run could be attached to a stranger's thread. It now answers
404 and writes no run row.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.dependencies import get_chat_service, get_runs_service
from src.main import create_app
from src.modules.auth.state import DEFAULT_USER_ID
from src.modules.chat.service import ChatService
from src.modules.runs.repository import AgentConfigDTO
from src.modules.runs.service import RunsService

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}


def _config() -> AgentConfigDTO:
    """Return a minimal config the in-memory journal can hold."""
    return AgentConfigDTO(
        id=uuid4(),
        name="ownership-agent",
        system_prompt="You answer briefly.",
        model="scripted-model",
        tools=["read_file"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={"ask_user": "external"},
        limits={"max_cost_micros": 1000},
    )


@pytest.fixture
def app():
    """A fresh app per test, so dependency overrides do not leak."""
    return create_app(Settings(DEBUG=True))


@pytest.mark.asyncio
async def test_post_runs_rejects_a_session_of_another_user(app) -> None:
    """A run cannot be attached to a thread the caller does not own."""
    runs = RunsService(session=None, settings=Settings(APP_ENV="test"))
    config = await runs.save_agent_config(_config())
    chat = ChatService(session=None, settings=Settings(APP_ENV="test"))

    stranger = await chat.create_session(uuid4(), "Someone else's thread")
    mine = await chat.create_session(DEFAULT_USER_ID, "My thread")

    app.dependency_overrides[get_runs_service] = lambda: runs
    app.dependency_overrides[get_chat_service] = lambda: chat

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/v1/runs",
            json={
                "config_id": str(config.id),
                "initial_prompt": "read their thread",
                "session_id": str(stranger.session_id),
            },
            headers=AUTH_HEADERS,
        )
        assert rejected.status_code == 404
        assert "Chat session" in rejected.json()["message"]
        assert runs.journal.runs == {}, "a rejected run must not create a row"

        unknown = await client.post(
            "/api/v1/runs",
            json={"config_id": str(config.id), "initial_prompt": "hi", "session_id": str(uuid4())},
            headers=AUTH_HEADERS,
        )
        assert unknown.status_code == 404
        assert runs.journal.runs == {}

        accepted = await client.post(
            "/api/v1/runs",
            json={
                "config_id": str(config.id),
                "initial_prompt": "read my thread",
                "session_id": str(mine.session_id),
            },
            headers=AUTH_HEADERS,
        )
        assert accepted.status_code == 201
        assert len(runs.journal.runs) == 1
        assert UUID(accepted.json()["id"]) in runs.journal.runs
