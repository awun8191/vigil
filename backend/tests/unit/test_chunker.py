"""Unit tests for window segmentation in the memory indexer."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.modules.memory.chunker import (
    Chunker,
    MemoryTurn,
)


class WordCounter:
    """Deterministic token counter: one token per whitespace word."""

    def count(self, text: str) -> int:
        return len(text.split())

    def truncate(self, text: str, max_tokens: int) -> str:
        words = text.split()
        if len(words) <= max_tokens:
            return text
        return " ".join(words[:max_tokens])


def _turns(count: int) -> list[MemoryTurn]:
    return [
        MemoryTurn(
            message_id=uuid4(),
            role="user" if index % 2 == 0 else "assistant",
            content=f"message {index} discusses the quarterly budget review",
        )
        for index in range(count)
    ]


def test_window_overlap() -> None:
    """A ten turn conversation produces two windows with one exchange of overlap."""
    turns = _turns(10)
    windows = Chunker(WordCounter()).segment(turns)

    assert len(windows) == 2
    assert [window.turn_count for window in windows] == [6, 6]

    first, second = windows
    assert first.turns == tuple(turns[0:6])
    assert second.turns == tuple(turns[4:10])

    overlap = set(first.source_message_ids) & set(second.source_message_ids)
    assert overlap == {turns[4].message_id, turns[5].message_id}
    assert len(overlap) == 2, "the overlap must be one exchange of two turns"

    covered = set(first.source_message_ids) | set(second.source_message_ids)
    assert covered == {turn.message_id for turn in turns}, "no turn may be dropped"


def test_chunk_token_cap() -> None:
    """A very long turn is truncated to the 512 token cap before encoding."""
    marker = "TAIL_MARKER_SHOULD_NOT_SURVIVE"
    long_turn = MemoryTurn(
        message_id=uuid4(),
        role="assistant",
        content=" ".join(["word"] * 4000) + f" {marker}",
    )
    counter = WordCounter()
    windows = Chunker(counter, max_tokens=512).segment([long_turn])

    assert len(windows) == 1
    assert counter.count(windows[0].text) <= 512
    assert marker not in windows[0].text
    assert windows[0].source_message_ids == (long_turn.message_id,)


def test_tools_not_embedded() -> None:
    """Tool results and system prompts never reach a chunk."""
    tool_turn = MemoryTurn(
        message_id=uuid4(),
        role="tool",
        content='SECRET_TOOL_OUTPUT {"command": "ls -la", "exit_code": 0}',
    )
    system_turn = MemoryTurn(
        message_id=uuid4(),
        role="system",
        content="SYSTEM_PROMPT_TEXT you are a helpful assistant",
    )
    conversation = _turns(6)
    fixture = [
        system_turn,
        conversation[0],
        conversation[1],
        tool_turn,
        conversation[2],
        conversation[3],
        conversation[4],
        conversation[5],
    ]

    windows = Chunker(WordCounter()).segment(fixture)

    assert windows, "the natural language turns must still produce chunks"
    joined = "\n".join(window.text for window in windows)
    assert "SECRET_TOOL_OUTPUT" not in joined
    assert "SYSTEM_PROMPT_TEXT" not in joined

    source_ids = {message_id for window in windows for message_id in window.source_message_ids}
    assert tool_turn.message_id not in source_ids
    assert system_turn.message_id not in source_ids
    for turn in conversation:
        assert turn.message_id in source_ids


@pytest.mark.parametrize("turn_count", [1, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13])
def test_every_turn_is_covered(turn_count: int) -> None:
    """Windows never lose a turn, and each window stays inside the four to six bound."""
    turns = _turns(turn_count)
    windows = Chunker(WordCounter()).segment(turns)

    assert windows
    covered = {message_id for window in windows for message_id in window.source_message_ids}
    assert covered == {turn.message_id for turn in turns}

    for window in windows:
        lower = min(4, turn_count)
        upper = min(6, turn_count)
        assert lower <= window.turn_count <= upper


def test_empty_input_produces_no_chunks() -> None:
    """An empty conversation and a tool-only conversation produce zero chunks."""
    chunker = Chunker(WordCounter())
    assert chunker.segment([]) == []
    assert chunker.segment([MemoryTurn(uuid4(), "tool", "shell output only")]) == []


def test_blank_turns_are_skipped() -> None:
    """Blank content carries no signal and is dropped."""
    blank = MemoryTurn(uuid4(), "user", "   \n  ")
    windows = Chunker(WordCounter()).segment([blank, *_turns(4)])
    assert windows
    assert blank.message_id not in {
        message_id for window in windows for message_id in window.source_message_ids
    }


def test_actor_labels_are_present() -> None:
    """Each chunk labels the actor so the embedding sees who spoke."""
    turns = [
        MemoryTurn(uuid4(), "user", "please draft the summary"),
        MemoryTurn(uuid4(), "assistant", "the summary is ready"),
    ]
    window = Chunker(WordCounter()).segment(turns)[0]
    assert "[User]: please draft the summary" in window.text
    assert "[Assistant]: the summary is ready" in window.text


def test_invalid_configuration_is_rejected() -> None:
    """A window smaller than its minimum or a full overlap is a programming error."""
    with pytest.raises(ValueError):
        Chunker(WordCounter(), window_turns=3, min_turns=4)
    with pytest.raises(ValueError):
        Chunker(WordCounter(), window_turns=6, overlap_turns=6)
    with pytest.raises(ValueError):
        Chunker(WordCounter(), max_tokens=0)
