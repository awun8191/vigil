"""Unit tests for the multi-tool barrier invariant.

A model turn can emit several tool calls at once. The provider rejects the next
request when one call has no answer, or when one call has two answers. The
barrier is the hard assertion that catches both cases before the model call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.modules.runs.barrier import (
    InvariantViolation,
    assert_barrier,
    assert_run_barrier,
    tool_call_ids,
)
from src.modules.runs.repository import MessageDTO


def assistant_message(*call_ids: str) -> MessageDTO:
    """Build an assistant message with the given tool call ids."""
    return MessageDTO(
        id=uuid4(),
        run_id=uuid4(),
        session_id=None,
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
            for call_id in call_ids
        ],
        tool_call_id=None,
        tool_name=None,
        tool_arguments=None,
        tool_result_reference=None,
        created_at=datetime.now(timezone.utc),
    )


def tool_row(call_id: str, content: str = '{"ok": true}') -> MessageDTO:
    """Build one tool row that answers a tool call id."""
    return MessageDTO(
        id=uuid4(),
        run_id=uuid4(),
        session_id=None,
        role="tool",
        content=content,
        tool_calls=None,
        tool_call_id=call_id,
        tool_name="read_file",
        tool_arguments=None,
        tool_result_reference=None,
        created_at=datetime.now(timezone.utc),
    )


def test_barrier_ok() -> None:
    """Three tool calls with three tool rows pass the barrier."""
    message = assistant_message("call-1", "call-2", "call-3")
    rows = [tool_row("call-1"), tool_row("call-2"), tool_row("call-3")]

    assert_barrier(message, rows)

    assert tool_call_ids(message) == ["call-1", "call-2", "call-3"]


def test_barrier_missing_id() -> None:
    """Two rows for three ids raise, and the error names the absent id."""
    message = assistant_message("call-1", "call-2", "call-3")
    rows = [tool_row("call-1"), tool_row("call-2")]

    with pytest.raises(InvariantViolation) as caught:
        assert_barrier(message, rows)

    assert caught.value.missing == ("call-3",)
    assert "call-3" in str(caught.value)


def test_barrier_duplicate_id() -> None:
    """Four rows for three ids raise, and the error names the repeated id."""
    message = assistant_message("call-1", "call-2", "call-3")
    rows = [
        tool_row("call-1"),
        tool_row("call-2"),
        tool_row("call-2"),
        tool_row("call-3"),
    ]

    with pytest.raises(InvariantViolation) as caught:
        assert_barrier(message, rows)

    assert caught.value.duplicate == ("call-2",)
    assert "call-2" in str(caught.value)


def test_barrier_passes_without_tool_calls() -> None:
    """A message with no tool call needs no tool row."""
    message = assistant_message()

    assert_barrier(message, [])

    assert tool_call_ids(message) == []


def test_barrier_ignores_rows_of_other_turns() -> None:
    """A row from an earlier turn does not answer a call of this turn."""
    message = assistant_message("call-3")
    rows = [tool_row("call-1"), tool_row("call-2"), tool_row("call-3")]

    assert_barrier(message, rows)


def test_barrier_rejects_a_repeated_id_in_one_message() -> None:
    """One message must not emit the same tool call id twice."""
    message = assistant_message("call-1", "call-1")

    with pytest.raises(InvariantViolation) as caught:
        assert_barrier(message, [tool_row("call-1"), tool_row("call-1")])

    assert caught.value.repeated == ("call-1",)


def test_barrier_rejects_a_call_without_id() -> None:
    """A tool call without an id can never be satisfied, so it raises."""
    message = assistant_message()
    message = MessageDTO(
        id=message.id,
        run_id=message.run_id,
        session_id=None,
        role="assistant",
        content=None,
        tool_calls=[{"type": "function", "function": {"name": "read_file"}}],
        tool_call_id=None,
        tool_name=None,
        tool_arguments=None,
        tool_result_reference=None,
        created_at=message.created_at,
    )

    with pytest.raises(InvariantViolation):
        assert_barrier(message, [])


def test_run_barrier_uses_the_last_assistant_turn() -> None:
    """The barrier reads the last assistant message, and the rows after it."""
    history = [
        assistant_message("call-1"),
        tool_row("call-1"),
        assistant_message("call-2", "call-3"),
        tool_row("call-2"),
    ]

    with pytest.raises(InvariantViolation) as caught:
        assert_run_barrier(history)

    assert caught.value.missing == ("call-3",)

    history.append(tool_row("call-3"))
    assert_run_barrier(history)


def test_run_barrier_passes_for_a_final_text_turn() -> None:
    """A last assistant turn with no tool call always passes."""
    history = [
        assistant_message("call-1"),
        tool_row("call-1"),
        MessageDTO(
            id=uuid4(),
            run_id=None,
            session_id=None,
            role="assistant",
            content="All done.",
            tool_calls=None,
            tool_call_id=None,
            tool_name=None,
            tool_arguments=None,
            tool_result_reference=None,
            created_at=datetime.now(timezone.utc),
        ),
    ]

    assert_run_barrier(history)
