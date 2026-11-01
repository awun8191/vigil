"""The multi-tool barrier: every tool call id has exactly one tool row.

A model turn can emit several tool calls at once. The provider rejects the next
request when the history holds an unanswered tool call, or a duplicate answer.
The barrier is a hard assertion before every model call. It reads the database
rows only, so it works after a restart with no memory held.

A missing id and a duplicated id both raise :class:`InvariantViolation`. The run
stops. The barrier never retries, because a retry would send the same incomplete
history to the model again.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "InvariantViolation",
    "assert_barrier",
    "assert_run_barrier",
    "tool_call_ids",
]


class InvariantViolation(RuntimeError):
    """Raised when the message history breaks the tool call barrier.

    The error carries the ids that caused the failure. A caller must not retry
    it. The run moves to ``failed`` and an operator inspects the rows.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str] = (),
        duplicate: Sequence[str] = (),
        repeated: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.missing: tuple[str, ...] = tuple(missing)
        self.duplicate: tuple[str, ...] = tuple(duplicate)
        self.repeated: tuple[str, ...] = tuple(repeated)


def _field(item: Any, name: str) -> Any:
    """Read one field from a mapping or from an attribute object."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _tool_calls(message: Any) -> list[Any]:
    """Return the raw tool call list of one message. An absent list is empty."""
    return list(_field(message, "tool_calls") or [])


def _call_id(call: Any) -> str:
    """Return the id of one tool call. A call without an id breaks the contract."""
    value = _field(call, "id")
    if value is None or str(value) == "":
        raise InvariantViolation("a tool call has no id, so no tool row can answer it")
    return str(value)


def tool_call_ids(assistant_message: Any) -> list[str]:
    """
    Return the tool call ids of one assistant message, in call order.

    Args:
        assistant_message: A message row or mapping with a ``tool_calls`` field.

    Returns:
        The ids in the order that the model emitted them.

    Raises:
        InvariantViolation: One call has no id.
    """
    return [_call_id(call) for call in _tool_calls(assistant_message)]


def assert_barrier(assistant_message: Any, tool_messages: Sequence[Any]) -> None:
    """
    Assert that every tool call id of one assistant message has one tool row.

    Args:
        assistant_message: The assistant message that holds the tool calls.
        tool_messages: Tool rows to test. Rows of other turns are ignored.

    Raises:
        InvariantViolation: An id is absent, an id has two rows, or the
            assistant message repeats an id.
    """
    ids = tool_call_ids(assistant_message)
    if not ids:
        return

    repeated = sorted(cid for cid, count in Counter(ids).items() if count > 1)
    if repeated:
        raise InvariantViolation(
            "the assistant message repeats a tool call id: " + ", ".join(repeated),
            repeated=repeated,
        )

    wanted = set(ids)
    counts: Counter[str] = Counter()
    for row in tool_messages:
        row_id = _field(row, "tool_call_id")
        if row_id is None:
            continue
        row_id = str(row_id)
        if row_id in wanted:
            counts[row_id] += 1

    missing = [cid for cid in ids if counts[cid] == 0]
    if missing:
        raise InvariantViolation(
            "the history has no tool row for: " + ", ".join(missing), missing=missing
        )

    duplicate = [cid for cid in ids if counts[cid] > 1]
    if duplicate:
        raise InvariantViolation(
            "the history has more than one tool row for: " + ", ".join(duplicate),
            duplicate=duplicate,
        )


def assert_run_barrier(messages: Sequence[Any]) -> None:
    """
    Assert the barrier for the last assistant message of a run history.

    The tool rows of that turn are the messages after it. When the last
    assistant message holds no tool call, the barrier passes.

    Args:
        messages: The full message history of the run, in chronological order.

    Raises:
        InvariantViolation: The last assistant turn is not satisfied.
    """
    index = -1
    for position in range(len(messages) - 1, -1, -1):
        if _field(messages[position], "role") == "assistant":
            index = position
            break
    if index < 0:
        return

    assistant = messages[index]
    if not _tool_calls(assistant):
        return

    tail = [row for row in messages[index + 1 :] if _field(row, "role") == "tool"]
    assert_barrier(assistant, tail)
