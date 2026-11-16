"""Active memory and personalization tools: ``memory`` and ``memory_search``.

Adapted from ``NousResearch/hermes-agent``:
- ``memory`` reads and mutates persistent curated stores (``USER.md`` for user profile
  and evolving opinion of the user, ``MEMORY.md`` for durable agent notes, and
  ``SOUL.md`` for agent personality/voice when requested by the user) with atomic
  batch operations and strict character budgets. When the user's name is learned,
  it also syncs to ``UserModel.display_name`` in PostgreSQL.
- ``memory_search`` actively queries three layers of memory:
  1. Curated ``USER.md`` and ``MEMORY.md`` entries.
  2. Long-term pgvector semantic + time-decay chunks via ``MemoryService.search_memory``.
  3. Recent conversation transcripts across sessions in ``MessageModel`` (so even turns
     that have not yet been background-indexed are immediately searchable).
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.config.settings import get_settings
from src.modules.memory.store import MemoryStore, MemoryTarget
from src.modules.runs.tools import Tool, ToolContext, ToolResult

MemoryAction = Literal["add", "patch", "remove", "read"]


class MemoryOperationItem(BaseModel):
    """One atomic operation in a ``memory`` batch call."""

    model_config = ConfigDict(extra="forbid")

    action: MemoryAction = Field(
        ...,
        description="Operation to perform: 'add', 'patch', 'remove', or 'read'.",
    )
    content: str | None = Field(
        default=None,
        max_length=8000,
        description=(
            "Required for 'add' and 'patch'. For 'add', the new entry text. "
            "For 'patch', the replacement text for old_text."
        ),
    )
    old_text: str | None = Field(
        default=None,
        max_length=8000,
        description=(
            "Required for 'patch' and 'remove': a unique substring from the target entry. "
            "'patch' replaces this exact span; 'remove' deletes the whole matching entry."
        ),
    )


class MemoryToolArgs(BaseModel):
    """Arguments for the ``memory`` tool."""

    model_config = ConfigDict(extra="forbid")

    target: MemoryTarget = Field(
        default="memory",
        description=(
            "Which store to read or update: "
            "'user' for USER.md (user name, role, preferences, and your evolving opinion/impression of the user), "
            "'memory' for MEMORY.md (durable environment facts, project conventions, tool quirks, lessons), "
            "or 'soul' for SOUL.md (your core personality/voice markdown when the user asks to customize it)."
        ),
    )
    operations: list[MemoryOperationItem] | None = Field(
        default=None,
        max_length=25,
        description=(
            "List of {action, content?, old_text?} operations applied atomically against the target's "
            "character budget. A single change can be passed as a 1-item list."
        ),
    )
    action: MemoryAction | None = Field(
        default=None,
        description="Single-operation shorthand ('add', 'patch', 'remove', or 'read') when 'operations' is omitted.",
    )
    content: str | None = Field(
        default=None,
        max_length=8000,
        description="Single-operation shorthand content for 'add' or 'patch'.",
    )
    old_text: str | None = Field(
        default=None,
        max_length=8000,
        description="Single-operation shorthand target substring for 'patch' or 'remove'.",
    )
    user_name: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Optional user display name when learned from conversation (e.g., 'Alex'). "
            "Saves 'Name: <user_name>' into USER.md and updates the user's profile display name."
        ),
    )


class MemorySearchArgs(BaseModel):
    """Arguments for the ``memory_search`` tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural language query or keywords to search across persistent memory and past conversations.",
    )
    top_n: int = Field(
        default=8,
        ge=1,
        le=25,
        description="Maximum number of results to return per memory layer.",
    )
    scopes: list[str] = Field(
        default_factory=lambda: ["global"],
        max_length=10,
        description="Memory scopes to search within (defaults to ['global']).",
    )


async def _sync_user_display_name(
    session: Any | None,
    user_id: UUID | None,
    display_name: str,
) -> bool:
    """Update ``UserModel.display_name`` in PostgreSQL when a session and user_id exist."""
    if session is None or user_id is None or not display_name.strip():
        return False
    try:
        from sqlalchemy import update
        from src.schemas.identity import UserModel

        stmt = (
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(display_name=display_name.strip()[:255])
        )
        await session.execute(stmt)
        flush = getattr(session, "flush", None)
        if callable(flush):
            await flush()
        return True
    except Exception:  # noqa: BLE001 - best-effort DB sync; never fail the tool call
        return False


async def memory_handler(args: MemoryToolArgs, context: ToolContext) -> ToolResult:
    """Execute ``memory`` operations against ``MemoryStore`` and sync learned user name."""
    settings = get_settings()
    store = MemoryStore(settings=settings)

    ops: list[dict[str, Any]] = []
    if args.operations:
        ops = [item.model_dump(exclude_none=True) for item in args.operations]
    elif args.action is not None:
        single: dict[str, Any] = {"action": args.action}
        if args.content is not None:
            single["content"] = args.content
        if args.old_text is not None:
            single["old_text"] = args.old_text
        ops = [single]

    name_updated: str | None = None
    if args.user_name and args.user_name.strip():
        cleaned_name = args.user_name.strip()
        name_res = store.upsert_user_name(cleaned_name)
        if not name_res.get("success"):
            return ToolResult.failure(
                str(name_res.get("error") or "Failed to save user_name"),
                content=name_res,
            )
        name_updated = cleaned_name
        await _sync_user_display_name(context.session, context.user_id, cleaned_name)
        if not ops:
            name_res["user_name_synced"] = cleaned_name
            return ToolResult.success(name_res)

    if not ops:
        return ToolResult.failure(
            "Provide 'operations' (a list of {action: 'add'|'patch'|'remove'|'read', content?, old_text?}), "
            "a top-level 'action', or 'user_name'."
        )

    outcome = store.apply_batch(args.target, ops)
    if not outcome.get("success"):
        return ToolResult.failure(
            str(outcome.get("error") or "Memory operation failed"),
            content=outcome,
        )

    if args.target == "user":
        extracted_name = store.extract_user_name()
        if extracted_name:
            name_updated = extracted_name
            await _sync_user_display_name(context.session, context.user_id, extracted_name)

    if name_updated:
        outcome["user_name_synced"] = name_updated

    return ToolResult.success(outcome)


def _match_curated_entries(store: MemoryStore, query: str, top_n: int) -> list[dict[str, str]]:
    """Search ``USER.md`` and ``MEMORY.md`` entries for query tokens."""
    tokens = [tok.lower() for tok in query.split() if len(tok.strip()) >= 2]
    q_lower = query.strip().lower()
    matches: list[tuple[int, dict[str, str]]] = []

    for target in ("user", "memory"):
        for entry in store.get_entries(target):
            entry_lower = entry.lower()
            score = 0
            if q_lower and q_lower in entry_lower:
                score += 5
            score += sum(1 for tok in tokens if tok in entry_lower)
            if score > 0 or not tokens:
                matches.append((score, {"source": target, "content": entry}))

    matches.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in matches[:top_n]]


async def _search_recent_session_messages(
    session: Any | None,
    user_id: UUID | None,
    exclude_run_id: UUID | None,
    query: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Search recent conversation messages across the user's chat sessions."""
    if session is None or user_id is None:
        return []
    try:
        from sqlalchemy import or_, select
        from src.schemas.runtime import MessageModel, ThreadModel

        tokens = [
            tok.strip()
            for tok in query.replace(",", " ").split()
            if len(tok.strip()) >= 3
        ][:6]
        stmt = (
            select(
                MessageModel.id,
                MessageModel.session_id,
                ThreadModel.title,
                MessageModel.role,
                MessageModel.content,
                MessageModel.created_at,
            )
            .join(ThreadModel, ThreadModel.id == MessageModel.session_id)
            .where(
                ThreadModel.user_id == user_id,
                MessageModel.role.in_(("user", "assistant")),
                MessageModel.content.is_not(None),
            )
        )
        if exclude_run_id is not None:
            stmt = stmt.where(
                or_(
                    MessageModel.run_id.is_(None),
                    MessageModel.run_id != exclude_run_id,
                )
            )
        if tokens:
            stmt = stmt.where(
                or_(*[MessageModel.content.ilike(f"%{tok}%") for tok in tokens])
            )
        else:
            stmt = stmt.where(MessageModel.content.ilike(f"%{query.strip()}%"))

        stmt = stmt.order_by(MessageModel.created_at.desc()).limit(top_n)
        rows = (await session.execute(stmt)).all()
        results: list[dict[str, Any]] = []
        for row in rows:
            content = str(row[4] or "").strip()
            if not content:
                continue
            results.append(
                {
                    "message_id": str(row[0]),
                    "session_id": str(row[1]),
                    "session_title": row[2] or "Untitled session",
                    "role": row[3],
                    "excerpt": content[:600],
                    "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
                }
            )
        return results
    except Exception:  # noqa: BLE001 - graceful degradation when DB is mocked or offline
        return []


async def memory_search_handler(args: MemorySearchArgs, context: ToolContext) -> ToolResult:
    """Search curated memory files, pgvector semantic memory chunks, and recent session transcripts."""
    settings = get_settings()
    store = MemoryStore(settings=settings)

    curated_matches = _match_curated_entries(store, args.query, args.top_n)

    semantic_chunks: list[dict[str, Any]] = []
    retrieval_path: str | None = None
    if context.user_id is not None:
        try:
            from src.modules.memory.service import MemoryService

            service = MemoryService(session=context.session, settings=settings)
            results = await service.search_memory(
                user_id=context.user_id,
                query=args.query,
                scopes=list(args.scopes or ["global"]),
                top_n=args.top_n,
            )
            retrieval_path = service.last_retrieval_path
            semantic_chunks = [
                {
                    "id": str(item.id),
                    "content": item.content,
                    "summary": item.summary,
                    "similarity": round(float(item.similarity), 4),
                    "age_days": round(float(item.age_days), 2),
                    "final_score": round(float(item.final_score), 4),
                }
                for item in results
            ]
        except Exception:  # noqa: BLE001 - degrade gracefully if local embedder or DB is unavailable
            semantic_chunks = []

    session_messages = await _search_recent_session_messages(
        session=context.session,
        user_id=context.user_id,
        exclude_run_id=context.run_id,
        query=args.query,
        top_n=args.top_n,
    )

    return ToolResult.success(
        {
            "query": args.query,
            "curated_matches": curated_matches,
            "semantic_chunks": semantic_chunks,
            "session_messages": session_messages,
            "retrieval_path": retrieval_path,
            "total_matches": len(curated_matches) + len(semantic_chunks) + len(session_messages),
        }
    )


MEMORY_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="memory",
        description=(
            "Read or update persistent memory across sessions (USER.md, MEMORY.md, and SOUL.md). "
            "Use target='user' to record who the user is (name, role, preferences, communication style) "
            "and your candid, evolving opinion/impression of the user. "
            "Use target='memory' to save durable environment facts, project conventions, and lessons learned. "
            "Use target='soul' when the user asks to customize your personality, tone, or identity markdown. "
            "Pass changes in 'operations' ([{action: 'add'|'patch'|'remove'|'read', content?, old_text?}]). "
            "When you learn the user's name, pass user_name='<name>' (or add 'Name: <name>' to target='user') "
            "to update both USER.md and their profile display name."
        ),
        level="local",
        args_model=MemoryToolArgs,
        handler=memory_handler,
    ),
    Tool.define(
        name="memory_search",
        description=(
            "Search your long-term memory and past conversations. Queries curated USER.md/MEMORY.md entries, "
            "pgvector semantic memory chunks (with exponential time-decay ranking), and recent session transcripts. "
            "Call this whenever the user references a prior conversation, past decision, or personal context "
            "that is not already visible in the current turn."
        ),
        level="local",
        args_model=MemorySearchArgs,
        handler=memory_search_handler,
    ),
)


__all__ = [
    "MEMORY_TOOLS",
    "MemoryAction",
    "MemoryOperationItem",
    "MemorySearchArgs",
    "MemoryToolArgs",
    "memory_handler",
    "memory_search_handler",
]
