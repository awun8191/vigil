"""Unit tests for Hermes-adapted personalization (SOUL.md), curated memory (USER.md, MEMORY.md),
active harness tools (memory, memory_search), dynamic prompt assembly, and REST endpoints.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.main import create_app
from src.modules.memory.store import (
    DEFAULT_SOUL_MD,
    ENTRY_DELIMITER,
    MemoryStore,
)
from src.modules.runs.core_context import (
    build_core_system_prompt,
    compose_run_system_prompt,
)
from src.modules.runs.tools import ToolContext, invoke_tool
from tests.unit.test_tools import FakeRunner

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}


# ---------------------------------------------------------------------------
# 1. MemoryStore unit tests
# ---------------------------------------------------------------------------


def test_memory_store_seeds_and_updates_soul_md(tmp_path: Path) -> None:
    """SOUL.md is seeded on first load, strips YAML frontmatter for prompt, and validates char budget."""
    store = MemoryStore(home_dir=tmp_path, soul_char_limit=500)
    raw = store.load_soul_raw()
    assert "You are Vigil" in raw
    assert (tmp_path / "SOUL.md").is_file()

    # Save with YAML frontmatter; prompt loader strips frontmatter.
    res = store.save_soul("---\ntitle: Custom Soul\n---\n# Custom Persona\nSpeak like a terse pirate.")
    assert res["success"] is True
    assert store.load_soul_for_prompt() == "# Custom Persona\nSpeak like a terse pirate."

    # Exceeding budget is rejected without clobbering the file.
    overflow = store.save_soul("x" * 501)
    assert overflow["success"] is False
    assert "exceeds character limit" in overflow["error"]
    assert "Speak like a terse pirate." in store.load_soul_for_prompt()


def test_memory_store_atomic_batch_and_budget_enforcement(tmp_path: Path) -> None:
    """USER.md and MEMORY.md enforce char budgets on the final state of a batch."""
    store = MemoryStore(home_dir=tmp_path, user_char_limit=120, memory_char_limit=200)

    # Add two entries to USER.md
    res = store.apply_batch(
        "user",
        [
            {"action": "add", "content": "Name: Alex"},
            {"action": "add", "content": "Impression: values direct systems design and concrete tests."},
        ],
    )
    assert res["success"] is True
    assert len(res["entries"]) == 2
    assert store.extract_user_name() == "Alex"
    assert ENTRY_DELIMITER in (tmp_path / "memories" / "USER.md").read_text(encoding="utf-8")

    # Overflowing add is rejected and returns current_entries + usage
    too_long = store.apply_batch(
        "user",
        [{"action": "add", "content": "A" * 100}],
    )
    assert too_long["success"] is False
    assert "Character limit exceeded" in too_long["error"]
    assert len(too_long["current_entries"]) == 2

    # Atomic batch that removes/patches stale entry AND adds a new entry succeeds in one call!
    consolidated = store.apply_batch(
        "user",
        [
            {"action": "patch", "old_text": "values direct systems design and concrete tests.", "content": "systems thinker."},
            {"action": "add", "content": "Prefers Python 3.12 and TypeScript."},
        ],
    )
    assert consolidated["success"] is True
    assert consolidated["entries"] == [
        "Name: Alex",
        "Impression: systems thinker.",
        "Prefers Python 3.12 and TypeScript.",
    ]


def test_memory_store_remove_and_patch_validation(tmp_path: Path) -> None:
    """Patch and remove reject ambiguous or missing substrings cleanly."""
    store = MemoryStore(home_dir=tmp_path)
    store.set_entries("memory", ["Uses uv for Python packaging", "Uses uv for running pytest"])

    ambiguous = store.apply_batch("memory", [{"action": "remove", "old_text": "Uses uv"}])
    assert ambiguous["success"] is False
    assert "matched 2 entries" in ambiguous["error"]

    exact = store.apply_batch("memory", [{"action": "remove", "old_text": "running pytest"}])
    assert exact["success"] is True
    assert exact["entries"] == ["Uses uv for Python packaging"]


# ---------------------------------------------------------------------------
# 2. Active tools (`memory` and `memory_search`) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_tool_updates_user_opinion_name_and_soul(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `memory` tool updates USER.md, MEMORY.md, SOUL.md, and syncs user_name to DB."""
    monkeypatch.setenv("VIGIL_HOME", str(tmp_path))

    executed_statements: list[Any] = []

    class _FakeSession:
        async def execute(self, stmt: Any) -> Any:
            executed_statements.append(stmt)
            return None

        async def flush(self) -> None:
            return None

    user_id = uuid4()
    ctx = ToolContext(
        runner=FakeRunner(),
        container_id="host-only",
        delivery_dir=tmp_path,
        user_id=user_id,
        session=_FakeSession(),
    )

    # 1. Learn user's name and record initial opinion in USER.md
    res_user = await invoke_tool(
        "memory",
        {
            "target": "user",
            "user_name": "Nas",
            "operations": [
                {
                    "action": "add",
                    "content": "Impression: sharp builder who wants agents with real memory and agency.",
                }
            ],
        },
        ctx,
    )
    assert res_user.ok is True
    assert res_user.content["user_name_synced"] == "Nas"
    assert any("Name: Nas" in e for e in res_user.content["entries"])
    assert any("Impression: sharp builder" in e for e in res_user.content["entries"])
    assert len(executed_statements) >= 1

    # 2. Save an environment note in MEMORY.md
    res_mem = await invoke_tool(
        "memory",
        {
            "target": "memory",
            "action": "add",
            "content": "Backend uses FastAPI + SQLAlchemy 2.0 async with pgvector.",
        },
        ctx,
    )
    assert res_mem.ok is True
    assert res_mem.content["entries"] == [
        "Backend uses FastAPI + SQLAlchemy 2.0 async with pgvector."
    ]

    # 3. Customize SOUL.md via tool
    res_soul = await invoke_tool(
        "memory",
        {
            "target": "soul",
            "operations": [
                {
                    "action": "add",
                    "content": "## Custom Rule\nAlways challenge weak assumptions directly.",
                }
            ],
        },
        ctx,
    )
    assert res_soul.ok is True
    assert "Always challenge weak assumptions directly." in res_soul.content["content"]


@pytest.mark.asyncio
async def test_memory_search_tool_queries_curated_and_semantic_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`memory_search` returns matches from USER.md, MEMORY.md, and semantic memory."""
    monkeypatch.setenv("VIGIL_HOME", str(tmp_path))
    store = MemoryStore(home_dir=tmp_path)
    store.set_entries("user", ["Name: Nas", "Opinion: loves clean dark-mode editorial UIs"])
    store.set_entries("memory", ["Architecture: uses Hermes-style SOUL.md and USER.md"])

    ctx = ToolContext(
        runner=FakeRunner(),
        container_id="host-only",
        delivery_dir=tmp_path,
        user_id=uuid4(),
        session=None,
    )

    res = await invoke_tool("memory_search", {"query": "editorial UIs Hermes"}, ctx)
    assert res.ok is True
    assert res.content["total_matches"] >= 2
    sources = {m["source"] for m in res.content["curated_matches"]}
    assert "user" in sources
    assert "memory" in sources


# ---------------------------------------------------------------------------
# 3. Dynamic System Prompt Assembly & Auto-Recall tests
# ---------------------------------------------------------------------------


def test_compose_run_system_prompt_injects_soul_snapshot_and_recalled_chunks(
    tmp_path: Path,
) -> None:
    """`build_core_system_prompt` and `compose_run_system_prompt` inject SOUL.md, USER.md, MEMORY.md, and recalled chunks."""
    store = MemoryStore(home_dir=tmp_path)
    store.save_soul("# Vigil — Custom Soul\nYou are an opinionated systems co-pilot.")
    store.set_entries("user", ["Name: Nas", "Impression: prefers concise answers with code links."])
    store.set_entries("memory", ["Test suite runs via uv run pytest."])

    recalled = [
        {
            "id": str(uuid4()),
            "content": "User previously decided to store SOUL.md on disk under ~/.vigil/SOUL.md.",
            "final_score": 0.84,
            "age_days": 0.5,
        }
    ]

    prompt = compose_run_system_prompt(
        None,
        store=store,
        recalled_chunks=recalled,
    )
    assert prompt is not None
    assert prompt.startswith("# Vigil — Custom Soul")
    assert "Name: Nas" in prompt
    assert "Impression: prefers concise answers with code links." in prompt
    assert "Test suite runs via uv run pytest." in prompt
    assert "## RECALLED PAST CONTEXT" in prompt
    assert "User previously decided to store SOUL.md on disk" in prompt


# ---------------------------------------------------------------------------
# 4. Personalization REST API tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personalization_rest_endpoints_get_and_put(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET and PUT /api/v1/memory/personalization read and update SOUL.md, USER.md, MEMORY.md, and display_name."""
    monkeypatch.setenv("VIGIL_HOME", str(tmp_path))
    settings = Settings(APP_ENV="test", VIGIL_HOME=str(tmp_path))
    app = create_app(settings)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_res = await client.get("/api/v1/memory/personalization", headers=AUTH_HEADERS)
        assert get_res.status_code == 200
        initial = get_res.json()
        assert "You are Vigil" in initial["soul_md"]
        assert len(initial["presets"]) >= 4

        put_res = await client.put(
            "/api/v1/memory/personalization",
            headers=AUTH_HEADERS,
            json={
                "display_name": "Nas",
                "soul_md": "# Vigil — Custom Persona\nBe direct, candid, and insightful.",
                "user_entries": [
                    "Name: Nas",
                    "Opinion: values autonomous memory and configurable personality.",
                ],
                "memory_entries": [
                    "Repo uses Hermes-adapted MemoryStore.",
                ],
            },
        )
        assert put_res.status_code == 200
        updated = put_res.json()
        assert updated["display_name"] == "Nas"
        assert updated["soul_md"] == "# Vigil — Custom Persona\nBe direct, candid, and insightful."
        assert "Name: Nas" in updated["user_entries"]
        assert "Opinion: values autonomous memory and configurable personality." in updated["user_entries"]
        assert updated["memory_entries"] == ["Repo uses Hermes-adapted MemoryStore."]

        # Reset soul back to default
        reset_res = await client.put(
            "/api/v1/memory/personalization",
            headers=AUTH_HEADERS,
            json={"reset_soul": True},
        )
        assert reset_res.status_code == 200
        assert reset_res.json()["soul_md"] == DEFAULT_SOUL_MD.strip()


@pytest.mark.asyncio
async def test_list_exclude_and_delete_memory_chunks_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/memory/chunks lists stored chunks for the user and supports exclude/delete."""
    from datetime import datetime, timezone
    from src.dependencies import get_current_user, get_memory_service
    from src.modules.auth.repository import UserDTO
    from src.modules.memory.repository import MemoryChunkCreateDTO
    from src.modules.memory.service import MemoryService

    monkeypatch.setenv("VIGIL_HOME", str(tmp_path))
    settings = Settings(APP_ENV="test", VIGIL_HOME=str(tmp_path))
    app = create_app(settings)

    user_id = uuid4()
    now = datetime.now(timezone.utc)
    fake_user = UserDTO(id=user_id, display_name="Nas", settings={}, created_at=now, updated_at=now)
    mem_service = MemoryService(session=None, settings=settings)  # type: ignore[arg-type]
    saved = await mem_service.save_chunk(
        MemoryChunkCreateDTO(
            user_id=user_id,
            scope="global",
            content="User prefers dark editorial UI and concise responses.",
            source_message_ids=[],
            embedding=[0.1] * 384,
            embedding_model="bge-small-en-v1.5",
        )
    )

    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_memory_service] = lambda: mem_service

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/memory/chunks", headers=AUTH_HEADERS)
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == str(saved.id)
        assert body["items"][0]["excluded"] is False

        excluded = await client.patch(
            f"/api/v1/memory/chunks/{saved.id}/exclude",
            headers=AUTH_HEADERS,
            json={"excluded": True},
        )
        assert excluded.status_code == 200
        assert excluded.json()["excluded"] is True

        deleted = await client.delete(f"/api/v1/memory/chunks/{saved.id}", headers=AUTH_HEADERS)
        assert deleted.status_code == 204

        after = await client.get("/api/v1/memory/chunks", headers=AUTH_HEADERS)
        assert after.status_code == 200
        assert after.json()["total"] == 0
