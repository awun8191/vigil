"""Artifact stub format for bulk history units replaced by Tier 1.

A stub keeps the provenance a reader needs to find the full payload again:
the tool name, the command, the size, the exit code, a 200 character preview,
and the artifact id.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.modules.compaction.tiers import HistoryUnit

PREVIEW_CHARS = 200

STUB_TAIL = (
    "Full log stored in artifacts. "
    "Use deliver_file or read_file if detailed inspection is required."
)

_UNKNOWN = "unknown"


def format_size(size_bytes: int) -> str:
    """Render a byte count as a short human readable size."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


def build_preview(content: str, limit: int = PREVIEW_CHARS) -> str:
    """Return at most `limit` characters of the first content, on one line."""
    return " ".join(content.split())[:limit]


@dataclass(frozen=True)
class ArtifactStub:
    """Plain-text replacement for one bulk history unit."""

    artifact_id: str
    tool_name: str
    command: str
    size_bytes: int
    exit_code: int | None
    preview: str

    @classmethod
    def from_unit(cls, unit: HistoryUnit, limit: int = PREVIEW_CHARS) -> "ArtifactStub":
        """Build a stub from one history unit."""
        if not unit.artifact_id:
            raise ValueError(f"Unit {unit.unit_id} has no artifact id. A stub must point at an artifact.")
        return cls(
            artifact_id=unit.artifact_id,
            tool_name=unit.tool_name or _UNKNOWN,
            command=unit.command or "",
            size_bytes=unit.size_bytes if unit.size_bytes is not None else len(unit.content.encode("utf-8")),
            exit_code=unit.exit_code,
            preview=build_preview(unit.content, limit),
        )

    def render(self) -> str:
        """Return the stub text that replaces the bulk content."""
        exit_code = _UNKNOWN if self.exit_code is None else str(self.exit_code)
        header = (
            f'[Truncated Artifact #{self.artifact_id}: tool={self.tool_name}, '
            f'command="{self.command}", size={format_size(self.size_bytes)}, '
            f"exit_code={exit_code}]"
        )
        return f"{header}\nPreview: {self.preview}\n{STUB_TAIL}"


def stub_for_unit(unit: HistoryUnit, limit: int = PREVIEW_CHARS) -> str:
    """Return the stub text for one history unit."""
    return ArtifactStub.from_unit(unit, limit).render()
