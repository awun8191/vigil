"""Test that compaction effectively reduces tokens and generates summaries."""
import pytest
from uuid import uuid4
from src.config.settings import Settings
from src.dependencies import get_compaction_service
from src.integrations.repositories import LLMResponseDTO


class MockFastLLM:
    def __init__(self) -> None:
        self.called = False

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        tier: str = "fast",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        self.called = True
        return LLMResponseDTO(
            content="What happened:\nPrevious steps performed multiple checks and tasks.\nDecisions:\nNone.\nOpen items:\nNone.",
            tool_calls=[],
            prompt_tokens=50,
            completion_tokens=15,
            total_tokens=65,
            finish_reason="stop",
            model="fast-llm",
        )


@pytest.mark.asyncio
async def test_compaction_effectively_reduces_history_tokens():
    settings = Settings(APP_ENV="test")
    compaction = get_compaction_service(session=None, settings=settings)
    mock_fast = MockFastLLM()
    compaction.fast_llm = mock_fast

    run_id = uuid4()
    # Build a tool chain with intermediate bulk logs followed by 6 trailing turns
    history = [
        {"id": "t1-user", "role": "user", "content": "Run tests and inspect failures."},
        {
            "id": "t1-tool-1",
            "role": "tool",
            "tool_name": "bash",
            "content": "test execution output log line with detailed failure trace " * 120,
            "artifact_id": "art-1",
        },
        {
            "id": "t1-tool-2",
            "role": "tool",
            "tool_name": "bash",
            "content": "compilation stdout and dependency trees " * 120,
            "artifact_id": "art-2",
        },
        {
            "id": "t1-tool-final",
            "role": "tool",
            "tool_name": "bash",
            "content": "final test verdict: 2 failed, 40 passed.",
            "artifact_id": "art-final",
        },
    ]
    # Add 7 trailing turns so the first turn is outside the recent 6 turns window
    for turn in range(2, 9):
        history.append({"id": f"t{turn}-user", "role": "user", "content": f"Follow up request {turn}."})
        history.append({"id": f"t{turn}-assistant", "role": "assistant", "content": f"Assistant response {turn}."})

    context = await compaction.compact(run_id, history)

    # Verify that intermediate tool units were stubbed and summarizer ran
    assert context.units_stubbed >= 1 or context.units_dropped >= 1
    assert context.compacted_tokens < context.original_tokens
    assert mock_fast.called is True
    assert "Compaction Summary" in context.summary_block
