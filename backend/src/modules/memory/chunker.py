"""Window segmentation for the memory indexer.

The indexer turns conversational turns into overlapping windows. Each window is
one memory chunk. Rules from the spec:

* Only user and assistant turns are embedded. Tool rows never reach the chunker.
* A window holds four to six turns.
* Consecutive windows overlap by one exchange, which is two turns.
* A window is capped at 512 tokens before encoding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence, runtime_checkable
from uuid import UUID

EMBEDDED_ROLES = frozenset({"user", "assistant"})
DEFAULT_WINDOW_TURNS = 6
DEFAULT_MIN_TURNS = 4
DEFAULT_OVERLAP_TURNS = 2
DEFAULT_MAX_TOKENS = 512


@runtime_checkable
class TokenLimiter(Protocol):
    """Token cap contract, implemented by the embedding tokenizer."""

    def truncate(self, text: str, max_tokens: int) -> str:
        """Return the head of a text with no more than max_tokens tokens."""
        ...


@dataclass(frozen=True)
class MemoryTurn:
    """One conversational turn that may enter memory."""

    message_id: UUID
    role: str
    content: str


@dataclass(frozen=True)
class MemoryWindow:
    """A chunk candidate: formatted text plus the turns that produced it."""

    text: str
    turns: tuple[MemoryTurn, ...]

    @property
    def source_message_ids(self) -> tuple[UUID, ...]:
        """Message ids that this window covers."""
        return tuple(turn.message_id for turn in self.turns)

    @property
    def turn_count(self) -> int:
        """Number of turns in the window."""
        return len(self.turns)


class Chunker:
    """Segment turns into overlapping windows with a token cap."""

    def __init__(
        self,
        counter: TokenLimiter,
        *,
        window_turns: int = DEFAULT_WINDOW_TURNS,
        min_turns: int = DEFAULT_MIN_TURNS,
        overlap_turns: int = DEFAULT_OVERLAP_TURNS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if min_turns < 1 or window_turns < min_turns:
            raise ValueError("window_turns must be at or above min_turns and min_turns at least 1")
        if overlap_turns < 1 or overlap_turns >= window_turns:
            raise ValueError("overlap_turns must be at least 1 and below window_turns")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.counter = counter
        self.window_turns = window_turns
        self.min_turns = min_turns
        self.overlap_turns = overlap_turns
        self.max_tokens = max_tokens
        self.step = window_turns - overlap_turns

    @staticmethod
    def is_embeddable(turn: MemoryTurn) -> bool:
        """Tool results, shell logs, and empty rows never enter memory."""
        return turn.role in EMBEDDED_ROLES and bool(turn.content.strip())

    def segment(self, turns: Iterable[MemoryTurn]) -> list[MemoryWindow]:
        """Build the chunk windows for a run of turns."""
        usable = [turn for turn in turns if self.is_embeddable(turn)]
        total = len(usable)
        if total == 0:
            return []

        spans: list[tuple[int, int]] = []
        start = 0
        while start < total:
            end = min(start + self.window_turns, total)
            if end - start < self.min_turns:
                # Widen the tail backwards so no turn is lost and the window
                # stays at or above the minimum length.
                start = max(0, total - self.min_turns)
                end = total
            if spans and start >= spans[-1][0] and end <= spans[-1][1]:
                # The tail is already covered by the previous window.
                break
            spans.append((start, end))
            if end >= total:
                break
            start += self.step

        windows: list[MemoryWindow] = []
        for window_start, window_end in spans:
            window_turns = tuple(usable[window_start:window_end])
            text = self._format(window_turns)
            if not text:
                continue
            windows.append(
                MemoryWindow(text=self.counter.truncate(text, self.max_tokens), turns=window_turns)
            )
        return windows

    @staticmethod
    def _format(turns: Sequence[MemoryTurn]) -> str:
        """Label each turn with its actor role."""
        lines = []
        for turn in turns:
            label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"[{label}]: {turn.content.strip()}")
        return "\n".join(lines)
