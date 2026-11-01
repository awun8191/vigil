"""Post-compaction verification of the assembled message history.

The compaction pipeline can drop a unit by mistake. A lost approval, or a
tool call with no result, makes the history illegal. This module checks the
assembled history against the source history before any model call.

Two assertions run after assembly:
1. Every unresolved approval of the source history is present.
2. Every tool call id of the last assistant turn has a result message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# Kinds that mark a message as an approval record.
APPROVAL_KINDS = frozenset({"approval", "approval_required"})

# Statuses that close an approval. Any other status leaves the approval open.
RESOLVED_STATUSES = frozenset({"approved", "rejected", "stale", "expired"})

DEFAULT_APPROVAL_STATUS = "pending"

_TOOL_ROLES = frozenset({"tool", "tool_result", "function"})

_KIND_BY_ROLE: dict[str, str] = {
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "tool": "tool_result",
    "tool_result": "tool_result",
    "function": "tool_result",
    "approval": "approval",
    "approval_required": "approval",
    "decision": "decision",
    "system": "system",
}


class CompactionVerificationError(RuntimeError):
    """Raised when the compacted history is not a legal message history."""

    def __init__(
        self,
        message: str,
        missing_approval_ids: Sequence[str] = (),
        unsatisfied_tool_call_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.missing_approval_ids: tuple[str, ...] = tuple(missing_approval_ids)
        self.unsatisfied_tool_call_ids: tuple[str, ...] = tuple(unsatisfied_tool_call_ids)


@dataclass(frozen=True)
class VerificationReport:
    """What the post-compaction check verified."""

    approval_ids: tuple[str, ...]
    tool_call_ids: tuple[str, ...]
    message_count: int
    summary_present: bool


def message_kind(message: Mapping[str, Any]) -> str:
    """Return the unit kind of one message."""
    token = message.get("kind") or message.get("type")
    if isinstance(token, str):
        mapped = _KIND_BY_ROLE.get(token)
        if mapped is not None:
            return mapped
        return token
    role = str(message.get("role", ""))
    return _KIND_BY_ROLE.get(role, role)


def message_id(message: Mapping[str, Any]) -> str | None:
    """Return the identity of one message, or None."""
    for key in ("id", "message_id", "unit_id", "approval_id", "tool_call_id"):
        value = message.get(key)
        if value:
            return str(value)
    return None


def identity_set(messages: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Return every identity that a message can carry."""
    ids: set[str] = set()
    for message in messages:
        for key in ("id", "message_id", "unit_id", "approval_id", "tool_call_id"):
            value = message.get(key)
            if value:
                ids.add(str(value))
    return frozenset(ids)


def approval_status(message: Mapping[str, Any]) -> str:
    """Return the status of one approval message."""
    for key in ("status", "decision", "approval_status"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value.strip().lower()
    return DEFAULT_APPROVAL_STATUS


def unresolved_approval_ids(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Return the ids of the approvals that are still open.

    An approval without a status counts as open, because a lost approval is
    worse than a large prompt.
    """
    ids: list[str] = []
    for message in messages:
        if message_kind(message) not in APPROVAL_KINDS:
            continue
        if approval_status(message) in RESOLVED_STATUSES:
            continue
        identity = message_id(message)
        if identity is not None:
            ids.append(identity)
    return tuple(ids)


def tool_call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the tool call ids of one assistant message."""
    calls = message.get("tool_calls") or ()
    ids: list[str] = []
    for call in calls:
        if isinstance(call, Mapping):
            value = call.get("id")
        else:
            value = getattr(call, "id", None)
        if value:
            ids.append(str(value))
    return tuple(ids)


def last_assistant_index(messages: Sequence[Mapping[str, Any]]) -> int | None:
    """Return the index of the last assistant message, or None."""
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role", "")) in {"assistant", "ai"}:
            return index
    return None


def satisfied_tool_call_ids(
    messages: Sequence[Mapping[str, Any]],
    after_index: int,
) -> frozenset[str]:
    """Return the tool call ids that have a result message after `after_index`."""
    satisfied: set[str] = set()
    for message in messages[after_index + 1 :]:
        if str(message.get("role", "")) not in _TOOL_ROLES:
            continue
        value = message.get("tool_call_id")
        if value:
            satisfied.add(str(value))
    return frozenset(satisfied)


def verify_compacted_history(
    assembled: Sequence[Mapping[str, Any]],
    original: Sequence[Mapping[str, Any]],
) -> VerificationReport:
    """Check the compacted history and return a report.

    Raises `CompactionVerificationError` when an unresolved approval is absent
    or when a tool call of the last assistant turn has no result.
    """
    present = identity_set(assembled)
    required_approvals = unresolved_approval_ids(original)
    missing_approvals = tuple(
        approval_id for approval_id in required_approvals if approval_id not in present
    )

    assistant_index = last_assistant_index(assembled)
    required_calls: tuple[str, ...] = ()
    missing_calls: tuple[str, ...] = ()
    if assistant_index is not None:
        required_calls = tool_call_ids(assembled[assistant_index])
        if required_calls:
            satisfied = satisfied_tool_call_ids(assembled, assistant_index)
            missing_calls = tuple(
                call_id for call_id in required_calls if call_id not in satisfied
            )

    summary_present = any(
        message_kind(message) == "summary" or message.get("id") == "compaction-summary"
        for message in assembled
    )

    if missing_approvals or missing_calls:
        problems: list[str] = []
        if missing_approvals:
            problems.append(
                "missing unresolved approvals: " + ", ".join(missing_approvals)
            )
        if missing_calls:
            problems.append(
                "tool calls without a result: " + ", ".join(missing_calls)
            )
        raise CompactionVerificationError(
            "The compacted history is not legal. " + ". ".join(problems) + ".",
            missing_approval_ids=missing_approvals,
            unsatisfied_tool_call_ids=missing_calls,
        )

    return VerificationReport(
        approval_ids=required_approvals,
        tool_call_ids=required_calls,
        message_count=len(assembled),
        summary_present=summary_present,
    )
