"""Hermes-adapted file-backed memory and personality store (SOUL.md, USER.md, MEMORY.md).

Manages three persistent Markdown files under ``VIGIL_HOME`` (default ``~/.vigil``):
- ``SOUL.md``: Configurable agent identity, persona, voice, and behavioral style.
  Injected into Slot #1 of the system prompt at the start of every run.
- ``memories/USER.md``: Curated user profile (name, role, preferences, technical stack)
  and Vigil's candid, evolving opinion/impression of the user.
- ``memories/MEMORY.md``: Curated agent notes (environment facts, standing conventions,
  tool quirks, and lessons learned across sessions).

``USER.md`` and ``MEMORY.md`` store discrete entries separated by ``ENTRY_DELIMITER``
(``"\\n§\\n"``) and enforce strict character budgets so the frozen system-prompt snapshot
remains compact and high-signal. Mutations acquire an advisory file lock and write
atomically via a sibling temporary file + ``os.replace``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from src.config.settings import Settings, get_settings

ENTRY_DELIMITER = "\n§\n"

SOUL_CHAR_LIMIT = 8000
USER_CHAR_LIMIT = 1500
MEMORY_CHAR_LIMIT = 2200

MemoryTarget = Literal["user", "memory", "soul"]

MEMORY_BLOCK_HEADERS: dict[str, str] = {
    "user": "USER PROFILE (who the user is & your evolving opinion)",
    "memory": "MEMORY (your personal notes)",
}

DEFAULT_SOUL_MD = """# Vigil — Core Identity & Persona

You are Vigil, an autonomous, proactive AI collaborator operating within a Hark Pro-inspired living canvas.

## Voice & Disposition
- **Direct & Candid**: Match the length of your reply to the weight of the ask. Skip filler ("Great question", "I'd be happy to") and never restate the user's prompt back to them.
- **Intellectually Honest**: Agree because a claim is right, not because the user said it. When you spot a flaw, a simpler path, or a blind spot in the user's thinking, say so plainly and constructively.
- **Genuine Perspective**: You are allowed — and encouraged — to form your own evolving opinion of the user, their working style, their priorities, and their habits. Use that understanding to anticipate what they actually need, calibrate your depth, and speak to them like a trusted collaborator who knows them well.
- **Personal Continuity**: Learn the user's name, role, preferences, and ongoing goals naturally through conversation, and record them in your persistent user profile (`USER.md`) without making a ceremony of it.
"""

PERSONA_PRESETS: list[dict[str, str]] = [
    {
        "id": "default",
        "name": "Default Vigil",
        "description": "Direct, proactive, candid collaborator who forms genuine opinions and adapts to your style.",
        "content": DEFAULT_SOUL_MD.strip(),
    },
    {
        "id": "candid-partner",
        "name": "Candid & Opinionated Partner",
        "description": "Sharp, unvarnished intellectual sparring partner who challenges weak assumptions first.",
        "content": (
            "# Vigil — Candid & Opinionated Partner\n\n"
            "You are Vigil, a sharp, intellectually rigorous co-pilot and thinking partner.\n\n"
            "## Voice & Disposition\n"
            "- **Unvarnished Honesty**: Never flatter or hedge when a direct assessment is clearer. If an idea is over-engineered, brittle, or unfocused, say so immediately and propose the stronger alternative.\n"
            "- **Strong Opinions, Loosely Held**: Take a clear stance on trade-offs, architecture, and priorities. Form a sharp, evolving model of the user's strengths, blind spots, and taste in `USER.md`.\n"
            "- **Zero Fluff**: Lead with the conclusion or action. Keep prose tight, concrete, and high-signal."
        ),
    },
    {
        "id": "systems-architect",
        "name": "Concise Systems Architect",
        "description": "Minimalist staff-engineer persona focused on invariants, failure modes, and clean execution.",
        "content": (
            "# Vigil — Concise Systems Architect\n\n"
            "You are Vigil, a principal systems engineer and autonomous execution harness.\n\n"
            "## Voice & Disposition\n"
            "- **Precision First**: Communicate in crisp, technical prose. State invariants, failure modes, and verification steps explicitly.\n"
            "- **Simplicity & Surgical Execution**: Favor small, composable designs over abstraction layers. Let working artifacts and verified tool output speak louder than commentary.\n"
            "- **Operator Model**: Track the user's technical stack, conventions, and engineering standards in `USER.md` and `MEMORY.md` so you never have to ask twice."
        ),
    },
    {
        "id": "chief-of-staff",
        "name": "Proactive Chief of Staff",
        "description": "Warm, organized executive partner focused on synthesis, follow-through, and decision clarity.",
        "content": (
            "# Vigil — Proactive Chief of Staff\n\n"
            "You are Vigil, a thoughtful, high-agency chief of staff and research partner.\n\n"
            "## Voice & Disposition\n"
            "- **Anticipatory & Structured**: Synthesize complex threads into clear decisions, trade-offs, and next actions. Surface what changed, why it matters, and what to do next.\n"
            "- **Deep Personal Context**: Pay close attention to the user's communication style, schedule priorities, and recurring collaborators in `USER.md`.\n"
            "- **Calm Clarity**: Keep tone warm, composed, and decisive without corporate jargon."
        ),
    },
]

# Invisible Unicode bidi overrides and zero-width characters that can hide prompt injection.
_INVISIBLE_CONTROL_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)

# Common patterns for extracting the user's name from USER.md entries.
_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:user(?:'s)?\s+name|preferred\s+name|name)\s*[:=-]\s*([^\n.;,()]+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\b(?:user(?:'s)?\s+name\s+is|goes\s+by|called)\s+([A-Z][A-Za-z0-9_'-]{1,40}(?:\s+[A-Z][A-Za-z0-9_'-]{1,40})?)", re.MULTILINE),
)


def strip_yaml_frontmatter(content: str) -> str:
    """Drop optional ``---`` YAML frontmatter so only the Markdown body is injected."""
    cleaned = content.lstrip("\ufeff").strip()
    if cleaned.startswith("---"):
        end = cleaned.find("\n---", 3)
        if end != -1:
            closing = cleaned.find("\n", end + 4)
            if closing != -1:
                body = cleaned[closing + 1 :].strip()
                if body:
                    return body
    return cleaned


def _scan_memory_content(text: str) -> str:
    """Sanitize a memory entry or SOUL.md content.

    Strips BOM and invisible bidi/zero-width characters and collapses any
    embedded ``\\n§\\n`` delimiter inside a single entry so it cannot split
    into spoofed entries on reload.
    """
    cleaned = _INVISIBLE_CONTROL_RE.sub("", text or "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned


def resolve_vigil_home(settings: Settings | None = None) -> Path:
    """Resolve the writable ``VIGIL_HOME`` directory on disk.

    Expands ``~`` from ``settings.VIGIL_HOME`` (or the ``VIGIL_HOME`` env var).
    Falls back to ``<WORKSPACE_BASE_PATH>/vigil_home`` or ``/tmp/vigil/home``
    if the primary directory cannot be created in a sandboxed environment.
    """
    env_override = os.environ.get("VIGIL_HOME")
    cfg = settings or get_settings()
    raw = env_override or getattr(cfg, "VIGIL_HOME", "~/.vigil") or "~/.vigil"
    candidates = [
        Path(raw).expanduser(),
        Path(getattr(cfg, "WORKSPACE_BASE_PATH", "/tmp/vigil/workspaces")) / "vigil_home",
        Path("/tmp/vigil/home"),
    ]
    for candidate in candidates:
        try:
            (candidate / "memories").mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return candidates[-1]


@contextmanager
def _locked_file(lock_path: Path) -> Iterator[None]:
    """Acquire an advisory exclusive lock on ``lock_path`` when ``fcntl`` is available."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically via a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class MemoryStore:
    """Hermes-style file-backed store for ``SOUL.md``, ``USER.md``, and ``MEMORY.md``."""

    def __init__(
        self,
        home_dir: Path | str | None = None,
        *,
        settings: Settings | None = None,
        soul_char_limit: int | None = None,
        user_char_limit: int | None = None,
        memory_char_limit: int | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.home_dir = (
            Path(home_dir).expanduser()
            if home_dir is not None
            else resolve_vigil_home(cfg)
        )
        self.memories_dir = self.home_dir / "memories"
        self.soul_path = self.home_dir / "SOUL.md"
        self.user_path = self.memories_dir / "USER.md"
        self.memory_path = self.memories_dir / "MEMORY.md"

        self.soul_char_limit = int(
            soul_char_limit
            if soul_char_limit is not None
            else getattr(cfg, "SOUL_CHAR_LIMIT", SOUL_CHAR_LIMIT)
        )
        self.user_char_limit = int(
            user_char_limit
            if user_char_limit is not None
            else getattr(cfg, "USER_PROFILE_CHAR_LIMIT", USER_CHAR_LIMIT)
        )
        self.memory_char_limit = int(
            memory_char_limit
            if memory_char_limit is not None
            else getattr(cfg, "MEMORY_NOTES_CHAR_LIMIT", MEMORY_CHAR_LIMIT)
        )

    # ------------------------------------------------------------------ SOUL.md

    def ensure_soul_exists(self) -> Path:
        """Seed ``SOUL.md`` with ``DEFAULT_SOUL_MD`` if it does not exist yet."""
        self.home_dir.mkdir(parents=True, exist_ok=True)
        if not self.soul_path.exists():
            with _locked_file(self.home_dir / ".soul.lock"):
                if not self.soul_path.exists():
                    _atomic_write_text(self.soul_path, DEFAULT_SOUL_MD.strip() + "\n")
        return self.soul_path

    def load_soul_raw(self) -> str:
        """Return the raw contents of ``SOUL.md``, seeding the default file if missing."""
        self.ensure_soul_exists()
        try:
            text = self.soul_path.read_text(encoding="utf-8-sig")
        except OSError:
            return DEFAULT_SOUL_MD.strip()
        cleaned = _scan_memory_content(text)
        return cleaned if cleaned else DEFAULT_SOUL_MD.strip()

    def load_soul_for_prompt(self) -> str:
        """Return ``SOUL.md`` with YAML frontmatter stripped and capped to ``soul_char_limit``."""
        raw = self.load_soul_raw()
        body = strip_yaml_frontmatter(raw)
        if len(body) > self.soul_char_limit:
            body = body[: self.soul_char_limit].rstrip()
        return body

    def soul_usage(self) -> dict[str, int]:
        """Return character usage metadata for ``SOUL.md``."""
        current = len(self.load_soul_raw())
        return {"current": current, "limit": self.soul_char_limit}

    def save_soul(self, content: str) -> dict[str, Any]:
        """Validate and atomically overwrite ``SOUL.md``."""
        cleaned = _scan_memory_content(content)
        if not cleaned:
            return {
                "success": False,
                "target": "soul",
                "error": "SOUL.md content cannot be empty.",
                "usage": self.soul_usage(),
            }
        if len(cleaned) > self.soul_char_limit:
            return {
                "success": False,
                "target": "soul",
                "error": (
                    f"SOUL.md exceeds character limit ({len(cleaned)}/{self.soul_char_limit} chars). "
                    f"Shorten the personality markdown to at most {self.soul_char_limit} characters."
                ),
                "usage": {"current": len(cleaned), "limit": self.soul_char_limit},
            }
        with _locked_file(self.home_dir / ".soul.lock"):
            _atomic_write_text(self.soul_path, cleaned + "\n")
        return {
            "success": True,
            "target": "soul",
            "content": cleaned,
            "usage": {"current": len(cleaned), "limit": self.soul_char_limit},
        }

    def reset_soul(self) -> dict[str, Any]:
        """Reset ``SOUL.md`` back to ``DEFAULT_SOUL_MD``."""
        return self.save_soul(DEFAULT_SOUL_MD.strip())

    # ------------------------------------------------------- USER.md & MEMORY.md

    def _path_for(self, target: str) -> Path:
        if target == "user":
            return self.user_path
        if target == "memory":
            return self.memory_path
        if target == "soul":
            return self.soul_path
        raise ValueError(f"Unknown memory target {target!r}. Expected 'user', 'memory', or 'soul'.")

    def _limit_for(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        if target == "memory":
            return self.memory_char_limit
        if target == "soul":
            return self.soul_char_limit
        raise ValueError(f"Unknown memory target {target!r}.")

    @staticmethod
    def _serialize_entries(entries: Sequence[str]) -> str:
        cleaned_entries: list[str] = []
        for entry in entries:
            sanitized = _scan_memory_content(entry).replace(ENTRY_DELIMITER, "\n").strip()
            if sanitized:
                cleaned_entries.append(sanitized)
        return ENTRY_DELIMITER.join(cleaned_entries)

    @staticmethod
    def _parse_entries(raw_text: str) -> list[str]:
        cleaned = _scan_memory_content(raw_text)
        if not cleaned:
            return []
        parts = cleaned.split(ENTRY_DELIMITER)
        return [part.strip() for part in parts if part.strip()]

    def get_entries(self, target: Literal["user", "memory"]) -> list[str]:
        """Return the list of discrete entries from ``USER.md`` or ``MEMORY.md``."""
        path = self._path_for(target)
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except OSError:
            return []
        return self._parse_entries(raw)

    def get_raw(self, target: Literal["user", "memory"]) -> str:
        """Return the serialized ``\\n§\\n``-delimited string for ``USER.md`` or ``MEMORY.md``."""
        return self._serialize_entries(self.get_entries(target))

    def usage(self, target: MemoryTarget) -> dict[str, int]:
        """Return ``{current, limit}`` character count for ``target``."""
        if target == "soul":
            return self.soul_usage()
        raw = self.get_raw(target)
        return {"current": len(raw), "limit": self._limit_for(target)}

    def set_entries(self, target: Literal["user", "memory"], entries: Sequence[str]) -> dict[str, Any]:
        """Atomically replace all entries for ``target`` if within character budget."""
        limit = self._limit_for(target)
        normalized = [
            _scan_memory_content(e).replace(ENTRY_DELIMITER, "\n").strip()
            for e in entries
            if _scan_memory_content(e).strip()
        ]
        serialized = ENTRY_DELIMITER.join(normalized)
        if len(serialized) > limit:
            current_entries = self.get_entries(target)
            return {
                "success": False,
                "target": target,
                "error": (
                    f"Character limit exceeded for {target!r} ({len(serialized)}/{limit} chars). "
                    "Reissue as one batch that removes or shortens stale entries and adds the new entry together."
                ),
                "current_entries": current_entries,
                "usage": {"current": len(ENTRY_DELIMITER.join(current_entries)), "limit": limit},
            }
        path = self._path_for(target)
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        with _locked_file(self.memories_dir / f".{target}.lock"):
            _atomic_write_text(path, (serialized + "\n") if serialized else "")
        return {
            "success": True,
            "target": target,
            "entries": normalized,
            "usage": {"current": len(serialized), "limit": limit},
        }

    def set_raw(self, target: Literal["user", "memory"], raw_markdown: str) -> dict[str, Any]:
        """Parse ``raw_markdown`` (supporting ``\\n§\\n`` delimiter) and persist entries."""
        entries = self._parse_entries(raw_markdown)
        return self.set_entries(target, entries)

    # --------------------------------------------------------- Batch Operations

    def apply_batch(
        self,
        target: MemoryTarget,
        operations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Apply a list of ``{action, content?, old_text?}`` operations atomically.

        Adapted from Hermes Agent's ``MemoryStore.apply_batch``:
        - Checks the character budget only on the **final** resulting state, so a
          single batch call can remove/patch stale entries and add new ones even
          when an ``add`` alone would overflow.
        - Supports ``target="soul"`` (``read``, ``patch``, ``add``/``replace``)
          as well as ``target="user"`` and ``target="memory"``.
        """
        if target not in ("user", "memory", "soul"):
            return {
                "success": False,
                "error": f"Invalid target {target!r}. Expected 'user', 'memory', or 'soul'.",
            }
        if not operations:
            return {
                "success": False,
                "target": target,
                "error": "No operations provided. Supply at least one operation ({action: 'add'|'patch'|'remove'|'read'}).",
            }

        if target == "soul":
            return self._apply_soul_batch(operations)

        entries = list(self.get_entries(target))
        limit = self._limit_for(target)
        applied_ops: list[str] = []

        for idx, raw_op in enumerate(operations, start=1):
            if not isinstance(raw_op, Mapping):
                return {
                    "success": False,
                    "target": target,
                    "error": f"Operation #{idx} must be an object with 'action'.",
                    "current_entries": entries,
                    "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                }
            action = str(raw_op.get("action") or "").strip().lower()
            content = _scan_memory_content(
                str(raw_op.get("content") or raw_op.get("new_text") or "")
            ).replace(ENTRY_DELIMITER, "\n").strip()
            old_text = _scan_memory_content(str(raw_op.get("old_text") or "")).strip()

            if action == "read":
                applied_ops.append("read")
                continue

            if action == "add":
                if not content:
                    return {
                        "success": False,
                        "target": target,
                        "error": f"Operation #{idx} ('add') requires non-empty 'content'.",
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                if content not in entries:
                    entries.append(content)
                applied_ops.append("add")
                continue

            if action == "patch":
                if not old_text:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('patch') requires 'old_text' (a unique substring of the entry to modify)."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                if not content:
                    return {
                        "success": False,
                        "target": target,
                        "error": f"Operation #{idx} ('patch') requires 'content' (replacement text for 'old_text').",
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                matches = [i for i, entry in enumerate(entries) if old_text in entry]
                if not matches:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('patch'): 'old_text' {old_text!r} was not found in any {target!r} entry."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                if len(matches) > 1:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('patch'): 'old_text' {old_text!r} matched {len(matches)} entries. "
                            "Provide a more specific substring."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                match_idx = matches[0]
                if entries[match_idx].count(old_text) > 1:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('patch'): 'old_text' {old_text!r} appears multiple times within the same entry. "
                            "Provide a longer span."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                patched = entries[match_idx].replace(old_text, content, 1).strip()
                if patched:
                    entries[match_idx] = patched
                else:
                    entries.pop(match_idx)
                applied_ops.append("patch")
                continue

            if action == "remove":
                if not old_text:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('remove') requires 'old_text' (a substring identifying the entry to remove)."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                matches = [i for i, entry in enumerate(entries) if old_text in entry]
                if not matches:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('remove'): 'old_text' {old_text!r} matched no entry in {target!r}."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                if len(matches) > 1:
                    return {
                        "success": False,
                        "target": target,
                        "error": (
                            f"Operation #{idx} ('remove'): 'old_text' {old_text!r} matched {len(matches)} entries. "
                            "Provide a more specific substring so only the intended entry is deleted."
                        ),
                        "current_entries": entries,
                        "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
                    }
                entries.pop(matches[0])
                applied_ops.append("remove")
                continue

            return {
                "success": False,
                "target": target,
                "error": f"Operation #{idx}: unknown action {action!r}. Use 'add', 'patch', 'remove', or 'read'.",
                "current_entries": entries,
                "usage": {"current": len(ENTRY_DELIMITER.join(entries)), "limit": limit},
            }

        if all(op == "read" for op in applied_ops):
            serialized = ENTRY_DELIMITER.join(entries)
            return {
                "success": True,
                "target": target,
                "applied": applied_ops,
                "entries": entries,
                "usage": {"current": len(serialized), "limit": limit},
            }

        write_result = self.set_entries(target, entries)
        if not write_result.get("success"):
            return write_result
        write_result["applied"] = applied_ops
        return write_result

    def _apply_soul_batch(self, operations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Apply ``read``, ``patch``, ``add``, or ``remove`` operations to ``SOUL.md``."""
        current = self.load_soul_raw()
        limit = self.soul_char_limit
        applied_ops: list[str] = []

        for idx, raw_op in enumerate(operations, start=1):
            action = str(raw_op.get("action") or "").strip().lower()
            content = _scan_memory_content(
                str(raw_op.get("content") or raw_op.get("new_text") or "")
            )
            old_text = _scan_memory_content(str(raw_op.get("old_text") or ""))

            if action == "read":
                applied_ops.append("read")
                continue
            if action == "add":
                if not content:
                    return {
                        "success": False,
                        "target": "soul",
                        "error": f"Operation #{idx} ('add') on soul requires 'content'.",
                        "content": current,
                        "usage": {"current": len(current), "limit": limit},
                    }
                # If content starts with a top-level heading (# ), treat as full replacement of SOUL.md;
                # otherwise append a new section/paragraph.
                if content.lstrip().startswith("# "):
                    current = content
                else:
                    current = f"{current.rstrip()}\n\n{content}".strip()
                applied_ops.append("add")
                continue
            if action == "patch":
                if not old_text or not content:
                    return {
                        "success": False,
                        "target": "soul",
                        "error": f"Operation #{idx} ('patch') on soul requires both 'old_text' and 'content'.",
                        "content": current,
                        "usage": {"current": len(current), "limit": limit},
                    }
                if old_text not in current:
                    return {
                        "success": False,
                        "target": "soul",
                        "error": f"Operation #{idx} ('patch'): 'old_text' not found in SOUL.md.",
                        "content": current,
                        "usage": {"current": len(current), "limit": limit},
                    }
                current = current.replace(old_text, content, 1).strip()
                applied_ops.append("patch")
                continue
            if action == "remove":
                if not old_text or old_text not in current:
                    return {
                        "success": False,
                        "target": "soul",
                        "error": f"Operation #{idx} ('remove'): 'old_text' not found in SOUL.md.",
                        "content": current,
                        "usage": {"current": len(current), "limit": limit},
                    }
                current = current.replace(old_text, "", 1).strip()
                applied_ops.append("remove")
                continue
            return {
                "success": False,
                "target": "soul",
                "error": f"Operation #{idx}: unknown action {action!r}. Use 'add', 'patch', 'remove', or 'read'.",
                "content": current,
                "usage": {"current": len(current), "limit": limit},
            }

        if all(op == "read" for op in applied_ops):
            return {
                "success": True,
                "target": "soul",
                "applied": applied_ops,
                "content": current,
                "usage": {"current": len(current), "limit": limit},
            }
        saved = self.save_soul(current)
        if saved.get("success"):
            saved["applied"] = applied_ops
        return saved

    # --------------------------------------------------------- Name Extraction

    def extract_user_name(self) -> str | None:
        """Extract the user's preferred name from ``USER.md`` entries if present."""
        for entry in self.get_entries("user"):
            for pattern in _NAME_PATTERNS:
                match = pattern.search(entry)
                if match:
                    candidate = match.group(1).strip().strip("\"'`")
                    if 1 <= len(candidate) <= 80:
                        return candidate
        return None

    def upsert_user_name(self, name: str) -> dict[str, Any]:
        """Ensure ``Name: <name>`` is recorded as the first entry in ``USER.md``."""
        cleaned_name = _scan_memory_content(name).replace("\n", " ").strip()
        if not cleaned_name:
            return {"success": False, "error": "Name cannot be empty."}
        entries = self.get_entries("user")
        name_line = f"Name: {cleaned_name}"
        replaced = False
        for idx, entry in enumerate(entries):
            if re.match(r"^\s*(?:user(?:'s)?\s+name|preferred\s+name|name)\s*[:=-]", entry, re.IGNORECASE):
                entries[idx] = name_line
                replaced = True
                break
        if not replaced:
            entries.insert(0, name_line)
        return self.set_entries("user", entries)

    # --------------------------------------------------- System Prompt Snapshot

    def render_system_prompt_snapshot(self) -> str:
        """Render the frozen ``USER.md`` and ``MEMORY.md`` snapshot block for the system prompt."""
        user_entries = self.get_entries("user")
        memory_entries = self.get_entries("memory")
        user_raw = ENTRY_DELIMITER.join(user_entries)
        memory_raw = ENTRY_DELIMITER.join(memory_entries)

        user_header = (
            f"### {MEMORY_BLOCK_HEADERS['user']} "
            f"[{len(user_raw)}/{self.user_char_limit} chars]"
        )
        memory_header = (
            f"### {MEMORY_BLOCK_HEADERS['memory']} "
            f"[{len(memory_raw)}/{self.memory_char_limit} chars]"
        )

        user_body = (
            "\n".join(f"- {entry}" for entry in user_entries)
            if user_entries
            else "(empty — call `memory_search` to check past conversations, and save learned name, preferences, and your evolving opinion with `memory(target='user', ...)`)"
        )
        memory_body = (
            "\n".join(f"- {entry}" for entry in memory_entries)
            if memory_entries
            else "(empty — save durable environment facts and conventions with `memory(target='memory', ...)`)"
        )

        return f"{user_header}\n{user_body}\n\n{memory_header}\n{memory_body}"


__all__ = [
    "DEFAULT_SOUL_MD",
    "ENTRY_DELIMITER",
    "MEMORY_BLOCK_HEADERS",
    "MEMORY_CHAR_LIMIT",
    "MemoryStore",
    "MemoryTarget",
    "PERSONA_PRESETS",
    "SOUL_CHAR_LIMIT",
    "USER_CHAR_LIMIT",
    "resolve_vigil_home",
    "strip_yaml_frontmatter",
]
