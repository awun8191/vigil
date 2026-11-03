"""Tier 0 safety locks and Tier 1 Jev scoring for context compaction.

Tier 0 is deterministic and has no probability gate. It locks every user
message, the last six conversation turns, every approval and decision, and
the final result of every tool chain. Tier 1 asks the Jev classifier three
calibrated questions per unit and turns each answer into one verdict.

Token counting uses the provider tokenizer for the configured model. This
module never estimates tokens from character counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping, Sequence

try:  # pragma: no cover - import guard only
    import tiktoken
    import tiktoken.model as _tiktoken_model

    _tiktoken_model.MODEL_PREFIX_TO_ENCODING.setdefault("muse-spark", "o200k_base")
except ImportError:  # pragma: no cover - tiktoken is a declared dependency
    tiktoken = None  # type: ignore[assignment]

from src.integrations.repositories import JevClientProtocol


# =============================================================================
# Constants fixed by the specification
# =============================================================================

RECENT_TURN_COUNT = 6

NEEDED_RETAIN_THRESHOLD = 0.7
USER_FACT_RETAIN_THRESHOLD = 0.7
BULK_STUB_THRESHOLD = 0.6
DROP_BELOW_THRESHOLD = 0.3

JEV_BATCH_SIZE = 50
MAX_JEV_CALLS = 3

DEFAULT_ENCODING_NAME = "o200k_base"


class TokenizerError(RuntimeError):
    """Raised when no provider tokenizer is available for the model."""


# =============================================================================
# History units
# =============================================================================

class UnitKind(str, Enum):
    """Category of one unit of conversation history."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    APPROVAL = "approval"
    DECISION = "decision"
    SYSTEM = "system"


class Verdict(str, Enum):
    """Outcome of the Tier 1 gate for one unit."""

    RETAIN = "retain"
    STUB = "stub"
    DROP = "drop"


_KIND_BY_ROLE: dict[str, UnitKind] = {
    "user": UnitKind.USER,
    "human": UnitKind.USER,
    "assistant": UnitKind.ASSISTANT,
    "ai": UnitKind.ASSISTANT,
    "tool": UnitKind.TOOL_RESULT,
    "tool_result": UnitKind.TOOL_RESULT,
    "function": UnitKind.TOOL_RESULT,
    "approval": UnitKind.APPROVAL,
    "decision": UnitKind.DECISION,
    "system": UnitKind.SYSTEM,
}

_LOCKED_KINDS = frozenset({UnitKind.USER, UnitKind.APPROVAL, UnitKind.DECISION})


@dataclass(frozen=True)
class HistoryUnit:
    """One addressable unit of conversation history.

    A unit is one message. Tool units carry the provenance that the artifact
    stub needs: tool name, command, size, exit code, and artifact id.
    """

    unit_id: str
    role: str
    content: str
    kind: UnitKind
    tool_name: str | None = None
    command: str | None = None
    exit_code: int | None = None
    artifact_id: str | None = None
    size_bytes: int | None = None

    @classmethod
    def from_message(cls, index: int, message: Mapping[str, Any]) -> "HistoryUnit":
        """Build a unit from an OpenAI-style message dictionary."""
        role = str(message.get("role", "assistant"))
        kind_token = message.get("kind") or message.get("type")
        if isinstance(kind_token, str) and kind_token in _KIND_BY_ROLE:
            kind = _KIND_BY_ROLE[kind_token]
        else:
            kind = _KIND_BY_ROLE.get(role, UnitKind.ASSISTANT)

        content = message.get("content")
        text = "" if content is None else str(content)

        unit_id = message.get("id") or f"unit-{index:04d}"

        exit_code = message.get("exit_code")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError):
                exit_code = None

        size_bytes = message.get("size_bytes")
        if size_bytes is None:
            size_bytes = len(text.encode("utf-8"))
        else:
            size_bytes = int(size_bytes)

        return cls(
            unit_id=str(unit_id),
            role=role,
            content=text,
            kind=kind,
            tool_name=_optional_str(message.get("tool_name")),
            command=_optional_str(message.get("command")),
            exit_code=exit_code,
            artifact_id=_optional_str(message.get("artifact_id")),
            size_bytes=size_bytes,
        )

    def is_locked_kind(self) -> bool:
        """True when the kind is always locked by Tier 0."""
        return self.kind in _LOCKED_KINDS

    def state(self) -> dict[str, Any]:
        """Return the classifier state for this unit."""
        return {
            "unit_id": self.unit_id,
            "role": self.role,
            "kind": self.kind.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "size_bytes": self.size_bytes,
        }


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def units_from_messages(messages: Sequence[Mapping[str, Any]]) -> list[HistoryUnit]:
    """Convert an OpenAI-style message list into ordered history units."""
    return [HistoryUnit.from_message(index, message) for index, message in enumerate(messages)]


# =============================================================================
# Tier 0: deterministic safety locks
# =============================================================================

@dataclass(frozen=True)
class Tier0Selection:
    """Set of unit ids that Tier 0 locks, with the reason for each lock."""

    locked_ids: frozenset[str]
    user_ids: frozenset[str]
    approval_ids: frozenset[str]
    decision_ids: frozenset[str]
    tool_chain_final_ids: frozenset[str]
    recent_turn_ids: frozenset[str]

    def is_locked(self, unit_id: str) -> bool:
        """True when the unit must never leave the active context."""
        return unit_id in self.locked_ids


def tool_chain_final_ids(units: Sequence[HistoryUnit]) -> frozenset[str]:
    """Return the id of the last tool result in every tool chain."""
    finals: list[str] = []
    run: list[HistoryUnit] = []
    for unit in units:
        if unit.kind is UnitKind.TOOL_RESULT:
            run.append(unit)
            continue
        if run:
            finals.append(run[-1].unit_id)
            run = []
    if run:
        finals.append(run[-1].unit_id)
    return frozenset(finals)


def recent_turn_ids(
    units: Sequence[HistoryUnit],
    recent_turns: int = RECENT_TURN_COUNT,
) -> frozenset[str]:
    """Return the unit ids of the last `recent_turns` turns.

    A turn starts at a user message and holds every unit up to the next user
    message. Units before the first user message form their own leading turn.
    """
    if recent_turns <= 0:
        return frozenset()

    groups: list[list[str]] = []
    for unit in units:
        if not groups or unit.kind is UnitKind.USER:
            groups.append([])
        groups[-1].append(unit.unit_id)

    return frozenset(unit_id for group in groups[-recent_turns:] for unit_id in group)


def lock_tier0_units(
    units: Sequence[HistoryUnit],
    recent_turns: int = RECENT_TURN_COUNT,
) -> Tier0Selection:
    """Build the Tier 0 lock set for a history.

    The lock covers every user message, the last six turns, every approval and
    every decision, and the final result of every tool chain.
    """
    user_ids = frozenset(unit.unit_id for unit in units if unit.kind is UnitKind.USER)
    approval_ids = frozenset(unit.unit_id for unit in units if unit.kind is UnitKind.APPROVAL)
    decision_ids = frozenset(unit.unit_id for unit in units if unit.kind is UnitKind.DECISION)
    finals = tool_chain_final_ids(units)
    recent = recent_turn_ids(units, recent_turns)

    return Tier0Selection(
        locked_ids=frozenset(user_ids | approval_ids | decision_ids | finals | recent),
        user_ids=user_ids,
        approval_ids=approval_ids,
        decision_ids=decision_ids,
        tool_chain_final_ids=finals,
        recent_turn_ids=recent,
    )


# =============================================================================
# Tier 1: Jev calibrated probability gate
# =============================================================================

TIER1_QUESTIONS: dict[str, str] = {
    "needed": "Is this unit needed to continue the current task?",
    "user_fact": "Does this unit contain a fact that the user supplied?",
    "bulk": "Is this unit raw bulk output, such as a log, a listing, or a large dump?",
}


def decide_verdict(probabilities: Mapping[str, float]) -> Verdict:
    """Map three calibrated probabilities to one verdict.

    A missing `needed` score means the gate cannot judge the unit, so the unit
    stays. The retain branch runs first, then the stub branch, then the drop
    branch.
    """
    needed = probabilities.get("needed")
    if needed is None:
        return Verdict.RETAIN
    user_fact = probabilities.get("user_fact", 0.0)
    bulk = probabilities.get("bulk", 0.0)

    if needed >= NEEDED_RETAIN_THRESHOLD or user_fact >= USER_FACT_RETAIN_THRESHOLD:
        return Verdict.RETAIN
    if bulk >= BULK_STUB_THRESHOLD:
        return Verdict.STUB
    if needed < DROP_BELOW_THRESHOLD:
        return Verdict.DROP
    return Verdict.RETAIN


@dataclass(frozen=True)
class UnitScore:
    """Tier 1 score and unlocked verdict for one unit."""

    unit_id: str
    needed: float
    user_fact: float
    bulk: float
    verdict: Verdict


@dataclass(frozen=True)
class Tier1Result:
    """Full Tier 1 outcome for one compaction pass."""

    scores: Mapping[str, UnitScore]
    jev_calls: int
    batch_sizes: tuple[int, ...]
    unscored_ids: frozenset[str]

    def score_for(self, unit_id: str) -> UnitScore | None:
        """Return the score of one unit, or None when the unit is unscored."""
        return self.scores.get(unit_id)


def _as_probability(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


async def score_units(
    units: Sequence[HistoryUnit],
    jev_client: JevClientProtocol | None,
    batch_size: int = JEV_BATCH_SIZE,
    max_calls: int = MAX_JEV_CALLS,
) -> Tier1Result:
    """Score units with Jev, in batches of at most 50 and at most 3 calls.

    Units that no call covers stay unscored. The caller retains every unscored
    unit, so the cap can never cause a silent removal.
    """
    if jev_client is None or not units:
        return Tier1Result(
            scores={},
            jev_calls=0,
            batch_sizes=(),
            unscored_ids=frozenset(unit.unit_id for unit in units),
        )

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_calls <= 0:
        raise ValueError("max_calls must be positive")

    scores: dict[str, UnitScore] = {}
    batch_sizes: list[int] = []
    pending = list(units)

    for start in range(0, min(len(pending), batch_size * max_calls), batch_size):
        batch = pending[start : start + batch_size]
        if not batch:
            break
        results = await jev_client.evaluate_batch(
            [unit.state() for unit in batch],
            dict(TIER1_QUESTIONS),
        )
        batch_sizes.append(len(batch))
        for unit, result in zip(batch, results):
            probabilities = getattr(result, "probabilities", {}) or {}
            needed = probabilities.get("needed")
            scores[unit.unit_id] = UnitScore(
                unit_id=unit.unit_id,
                needed=_as_probability(needed, 0.0) if needed is not None else 0.0,
                user_fact=_as_probability(probabilities.get("user_fact")),
                bulk=_as_probability(probabilities.get("bulk")),
                verdict=decide_verdict(probabilities),
            )

    unscored = frozenset(unit.unit_id for unit in units if unit.unit_id not in scores)
    return Tier1Result(
        scores=scores,
        jev_calls=len(batch_sizes),
        batch_sizes=tuple(batch_sizes),
        unscored_ids=unscored,
    )


def resolve_verdicts(
    units: Sequence[HistoryUnit],
    tier1: Tier1Result,
    tier0: Tier0Selection,
) -> dict[str, Verdict]:
    """Apply the Tier 0 lock to every Tier 1 verdict.

    This function is the single place where the safety lock is enforced. A
    locked unit keeps the verdict RETAIN whatever Tier 1 says.
    """
    resolved: dict[str, Verdict] = {}
    for unit in units:
        if tier0.is_locked(unit.unit_id):
            resolved[unit.unit_id] = Verdict.RETAIN
            continue
        score = tier1.score_for(unit.unit_id)
        resolved[unit.unit_id] = Verdict.RETAIN if score is None else score.verdict
    return resolved


# =============================================================================
# Provider tokenizer
# =============================================================================

@lru_cache(maxsize=16)
def _encoding(model: str):
    """Return the provider tokenizer encoding for a model name."""
    if tiktoken is None:
        raise TokenizerError("tiktoken is not installed. Token counting cannot use an estimate.")
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(DEFAULT_ENCODING_NAME)


class TokenCounter:
    """Counts tokens with the provider tokenizer for one model."""

    def __init__(self, model: str) -> None:
        if not model or not model.strip():
            raise TokenizerError("A model name is required to select the provider tokenizer.")
        self.model = model
        self._encoding = _encoding(model)

    @property
    def encoding_name(self) -> str:
        """Name of the provider encoding in use, for example o200k_base."""
        return self._encoding.name

    def count(self, text: str) -> int:
        """Return the provider token count of one string."""
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))

    def count_many(self, texts: Sequence[str]) -> int:
        """Return the summed provider token count of a sequence of strings."""
        return sum(self.count(text) for text in texts)
