"""Tests for single ambient session, JEV-free rolling compaction, and continuous memory overlap."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.config.settings import Settings
from src.dependencies import get_compaction_service
from src.integrations.repositories import LLMResponseDTO
from src.modules.chat.repository import ChatMessageCreateDTO
from src.modules.chat.service import ChatService
from src.modules.compaction.service import CompactionService
from src.modules.memory.service import IndexableMessage, MemoryService
from src.modules.runs.loop import RunLoop
from src.modules.runs.repository import MessageCreateDTO, RunCreateDTO
from src.modules.runs.state import InMemoryRunJournal
from tests.unit.loop_fakes import FakeToolExecutor, ScriptedModel, agent_config, text_response


class RecordingFastLLM:
    """Captures every Fast LLM summarizer call."""

    def __init__(self, reply: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply = reply or (
            "What happened:\n"
            "The user shared preferences and discussed early setup steps.\n"
            "Decisions:\n"
            "Use rolling compaction with an 8-interaction tail barrier.\n"
            "Open items:\n"
            "Continue the active implementation.\n"
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "fast",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        self.calls.append({"messages": messages, "tier": tier, "max_tokens": max_tokens})
        return LLMResponseDTO(content=self.reply, model="gpt-4o-mini")


class FakeEmbedder:
    model_id = "fake-bge"
    dim = 4

    def truncate(self, text: str, max_tokens: int) -> str:
        return text[: max_tokens * 4]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class FakeIndexStore:
    def __init__(
        self,
        already_indexed: list[IndexableMessage],
        unindexed: list[IndexableMessage],
    ) -> None:
        self.already_indexed = already_indexed
        self.unindexed = unindexed
        self.saved_chunks: list[Any] = []
        self.marked_ids: list[UUID] = []
        self.committed = False

    async def fetch_unindexed(self, session_id: UUID, limit: int) -> list[IndexableMessage]:
        return self.unindexed[:limit]

    async def fetch_recent_indexed(self, session_id: UUID, limit: int = 2) -> list[IndexableMessage]:
        return self.already_indexed[-limit:]

    async def add_chunks(self, chunks: Any) -> int:
        self.saved_chunks.extend(chunks)
        return len(chunks)

    async def mark_indexed(self, message_ids: list[UUID], indexed_at: datetime) -> int:
        self.marked_ids.extend(message_ids)
        return len(message_ids)

    async def rollback(self) -> None:
        self.saved_chunks.clear()

    async def commit(self) -> None:
        self.committed = True


def _build_interactions(count: int, *, include_noise: bool = False) -> list[dict[str, Any]]:
    base = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    idx = 0
    for turn in range(1, count + 1):
        out.append(
            {
                "id": uuid4(),
                "role": "user",
                "content": f"User question {turn}",
                "created_at": base + timedelta(seconds=idx),
            }
        )
        idx += 1
        if include_noise:
            out.append(
                {
                    "id": uuid4(),
                    "role": "assistant",
                    "content": "Let me run a SECRET_TOOL_CALL check.",
                    "tool_calls": [{"id": f"call-{turn}", "function": {"name": "bash"}}],
                    "created_at": base + timedelta(seconds=idx),
                }
            )
            idx += 1
            out.append(
                {
                    "id": uuid4(),
                    "role": "tool",
                    "tool_call_id": f"call-{turn}",
                    "content": f"RAW_TOOL_OUTPUT_SECRET_{turn}",
                    "created_at": base + timedelta(seconds=idx),
                }
            )
            idx += 1
        thinking_prefix = f"<think>SECRET_THINKING_TRACE_{turn}</think>\n" if include_noise else ""
        out.append(
            {
                "id": uuid4(),
                "role": "assistant",
                "content": f"{thinking_prefix}Assistant answer {turn}",
                "created_at": base + timedelta(seconds=idx),
            }
        )
        idx += 1
    return out


@pytest.mark.asyncio
async def test_primary_session_is_single_and_paginates_recent() -> None:
    chat = ChatService(session=None, settings=Settings(APP_ENV="test"))
    user_id = uuid4()

    first = await chat.get_or_create_primary_session(user_id)
    second = await chat.get_or_create_primary_session(user_id)
    assert first.session_id == second.session_id
    assert first.kind == "ambient"

    saved_ids: list[UUID] = []
    for i in range(6):
        msg = await chat.save_message(
            ChatMessageCreateDTO(
                session_id=first.session_id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg-{i}",
            )
        )
        saved_ids.append(msg.id)

    tail = await chat.get_recent_history(first.session_id, limit=3)
    assert [m.content for m in tail] == ["msg-3", "msg-4", "msg-5"]

    earlier = await chat.get_recent_history(first.session_id, limit=3, before_id=tail[0].id)
    assert [m.content for m in earlier] == ["msg-0", "msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_rolling_compaction_keeps_8_interactions_and_strips_tools_and_thinking() -> None:
    fast_llm = RecordingFastLLM()
    service = CompactionService(session=None, settings=Settings(), fast_llm=fast_llm)
    session_id = uuid4()

    # 8 interactions: below overflow threshold -> no compaction call
    eight = _build_interactions(8, include_noise=True)
    skipped = await service.compact_session_history(
        session_id, rolling_summary=None, uncompacted_messages=eight
    )
    assert skipped is None
    assert fast_llm.calls == []

    # 12 interactions: 4 overflow interactions above the 8-interaction barrier
    twelve = _build_interactions(12, include_noise=True)
    outcome = await service.compact_session_history(
        session_id,
        rolling_summary="Earlier summary: agreed on Postgres.",
        uncompacted_messages=twelve,
    )
    assert outcome is not None
    assert outcome.interactions_compacted == 4
    # 8 verbatim interactions * 2 clean messages = 16 messages kept
    assert len(outcome.kept_messages) == 16
    assert outcome.kept_messages[0]["content"] == "User question 5"
    assert "Assistant answer 12" in outcome.kept_messages[-1]["content"]

    assert len(fast_llm.calls) == 1
    prompt_text = fast_llm.calls[0]["messages"][-1]["content"]
    assert "Earlier summary: agreed on Postgres." in prompt_text
    assert "User question 1" in prompt_text
    assert "Assistant answer 4" in prompt_text
    assert "User question 5" not in prompt_text
    assert "RAW_TOOL_OUTPUT_SECRET" not in prompt_text
    assert "SECRET_TOOL_CALL" not in prompt_text
    assert "SECRET_THINKING_TRACE" not in prompt_text


@pytest.mark.asyncio
async def test_run_loop_triggers_session_rolling_compaction_and_injects_summary() -> None:
    journal = InMemoryRunJournal()
    config = agent_config()
    await journal.save_config(config)
    session_id = uuid4()
    base = datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc)

    # Seed 12 completed prior runs on the same single session
    for turn in range(1, 13):
        prior_run = await journal.create_run(RunCreateDTO(config_id=config.id))
        await journal.append_message(
            MessageCreateDTO(
                run_id=prior_run.id,
                session_id=session_id,
                role="user",
                content=f"Prior user turn {turn}",
            )
        )
        # Ensure strictly increasing timestamps for deterministic ordering
        journal.messages[prior_run.id][-1] = type(journal.messages[prior_run.id][-1])(
            **{
                **journal.messages[prior_run.id][-1].__dict__,
                "created_at": base + timedelta(seconds=turn * 2),
            }
        )
        await journal.append_message(
            MessageCreateDTO(
                run_id=prior_run.id,
                session_id=session_id,
                role="assistant",
                content=f"Prior assistant turn {turn}",
            )
        )
        journal.messages[prior_run.id][-1] = type(journal.messages[prior_run.id][-1])(
            **{
                **journal.messages[prior_run.id][-1].__dict__,
                "created_at": base + timedelta(seconds=turn * 2 + 1),
            }
        )

    fast_llm = RecordingFastLLM()
    compactor = CompactionService(session=None, settings=Settings(), fast_llm=fast_llm)
    reasoning_model = ScriptedModel([text_response("Turn 13 answer.")])

    run13 = await journal.create_run(RunCreateDTO(config_id=config.id))
    await journal.append_message(
        MessageCreateDTO(
            run_id=run13.id,
            session_id=session_id,
            role="user",
            content="Current turn 13 prompt",
        )
    )

    loop = RunLoop(
        journal=journal,
        model=reasoning_model,
        tools=FakeToolExecutor(),
        compactor=compactor,
    )
    await loop.iterate(run13.id)

    assert len(fast_llm.calls) == 1
    state = await journal.get_session_compaction_state(session_id)
    assert state is not None
    assert state.rolling_summary is not None
    assert "rolling compaction" in state.rolling_summary.lower()

    sent_messages = reasoning_model.calls[0]["messages"]
    assert any(
        m["role"] == "system" and "== Session Context Summary ==" in m["content"]
        for m in sent_messages
    )
    user_contents = [m["content"] for m in sent_messages if m["role"] == "user"]
    # Turns 1..4 were rolled off; turns 5..12 + current 13 remain
    assert "Prior user turn 1" not in user_contents
    assert "Prior user turn 4" not in user_contents
    assert "Prior user turn 5" in user_contents
    assert "Current turn 13 prompt" in user_contents


@pytest.mark.asyncio
async def test_memory_index_session_preserves_overlap_across_single_runs() -> None:
    session_id = uuid4()
    user_id = uuid4()
    old_1, old_2, new_1, new_2 = uuid4(), uuid4(), uuid4(), uuid4()
    store = FakeIndexStore(
        already_indexed=[
            IndexableMessage(old_1, session_id, user_id, "user", "Previous turn user."),
            IndexableMessage(old_2, session_id, user_id, "assistant", "Previous turn assistant."),
        ],
        unindexed=[
            IndexableMessage(new_1, session_id, user_id, "user", "New turn user."),
            IndexableMessage(new_2, session_id, user_id, "assistant", "New turn assistant."),
        ],
    )
    service = MemoryService(session=None, settings=Settings(), embedder=FakeEmbedder())  # type: ignore[arg-type]

    result = await service.index_session(session_id, store=store)
    assert result.messages_indexed == 2
    assert result.chunks_created == 1
    assert store.marked_ids == [new_1, new_2]
    assert store.saved_chunks[0].source_message_ids == [old_1, old_2, new_1, new_2]
    assert store.committed is True


def test_get_compaction_service_does_not_wire_jev() -> None:
    service = get_compaction_service(session=None, settings=Settings(APP_ENV="test"))  # type: ignore[arg-type]
    assert not hasattr(service, "jev_client")


@pytest.mark.asyncio
async def test_multi_tool_run_under_token_limit_does_not_spam_compaction() -> None:
    """A run with >10 messages (e.g. 7 tool retries) under token budget must not compact every turn."""
    from tests.unit.loop_fakes import text_response, tool_response

    journal = InMemoryRunJournal()
    config = agent_config(approval_policy={"echo": "local"})
    await journal.save_config(config)

    fast_llm = RecordingFastLLM()
    compactor = CompactionService(session=None, settings=Settings(), fast_llm=fast_llm)

    responses = [
        tool_response(("call-1", "echo", {"n": 1})),
        tool_response(("call-2", "echo", {"n": 2})),
        tool_response(("call-3", "echo", {"n": 3})),
        tool_response(("call-4", "echo", {"n": 4})),
        tool_response(("call-5", "echo", {"n": 5})),
        tool_response(("call-6", "echo", {"n": 6})),
        tool_response(("call-7", "echo", {"n": 7})),
        text_response("Finished all 7 tool calls."),
    ]
    model = ScriptedModel(responses)

    run = await journal.create_run(RunCreateDTO(config_id=config.id))
    await journal.append_message(
        MessageCreateDTO(run_id=run.id, role="user", content="Retry until panel works.")
    )

    loop = RunLoop(
        journal=journal,
        model=model,
        tools=FakeToolExecutor(),
        compactor=compactor,
    )
    finished = await loop.run(run.id)

    assert finished.status == "completed"
    steps = await journal.list_steps(run.id)
    compaction_steps = [s for s in steps if s.event_type == "compaction"]
    assert compaction_steps == []
    assert fast_llm.calls == []

