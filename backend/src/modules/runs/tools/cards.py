"""Card tool: ``raise_card``.

Lets the agent proactively surface Hark Pro observation/task cards on the user's
dashboard with an active verb, urgency, and one-tap CTAs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.modules.runs.tools import Tool, ToolContext, ToolResult


class RaiseCardActionArgs(BaseModel):
    """One interactive CTA button on a raised card."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=80)
    action_type: Literal["run", "reply", "dismiss"] = Field(...)
    verb: str | None = Field(default=None, max_length=32)
    initial_prompt: str | None = Field(default=None, max_length=2000)
    seed: str | None = Field(default=None, max_length=1000)


class RaiseCardArgs(BaseModel):
    """Arguments for the ``raise_card`` tool."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=250, description="Concise Hark Pro observation headline.")
    summary: str | None = Field(default=None, max_length=1000, description="1-2 sentence executive context.")
    type: Literal["email", "calendar", "inquiry", "billing", "deadline", "subscription", "meeting", "security", "general"] = Field(
        default="general"
    )
    urgency: Literal["critical", "high", "medium", "low"] = Field(default="medium")
    verb: str | None = Field(default="Review", max_length=32, description="Active Hark Pro verb (e.g. 'Draft', 'Reply', 'Accept', 'Review').")
    proactivity_tag: str | None = Field(default="Autonomous Observation", max_length=64)
    thread_id: str | None = Field(default=None, max_length=255, description="Optional external thread ID for deduplication.")
    due_at: str | None = Field(default=None, description="Optional ISO-8601 deadline.")
    actions: list[RaiseCardActionArgs] = Field(default_factory=list, max_length=3)


async def raise_card(args: RaiseCardArgs, context: ToolContext) -> ToolResult:
    """Create or update a proactive Hark Pro card for the run owner."""
    if context.user_id is None:
        return ToolResult.failure("raise_card requires an authenticated user_id in ToolContext")

    from src.config.settings import get_settings
    from src.modules.cards.repository import CardActionDTO, CardCreateDTO
    from src.modules.cards.service import CardsService

    due_dt: datetime | None = None
    if args.due_at:
        try:
            due_dt = datetime.fromisoformat(args.due_at.replace("Z", "+00:00"))
        except ValueError:
            return ToolResult.failure(f"Invalid due_at ISO-8601 timestamp: {args.due_at!r}")

    actions: list[CardActionDTO] = []
    for idx, act in enumerate(args.actions):
        payload: dict[str, str] = {}
        if act.action_type == "run":
            payload["initial_prompt"] = act.initial_prompt or f"Help me with: {args.title}"
        elif act.action_type == "reply":
            payload["seed"] = act.seed or f"Regarding '{args.title}': "
        actions.append(
            CardActionDTO(
                id=f"act_{idx}_{uuid4().hex[:6]}",
                label=act.label,
                action_type=act.action_type,
                verb=act.verb or args.verb,
                payload=payload,
            )
        )

    if not actions:
        actions = [
            CardActionDTO(
                id="act_primary",
                label=args.verb or "Review",
                action_type="run",
                verb=args.verb or "Review",
                payload={"initial_prompt": f"Help me with: {args.title}. {args.summary or ''}".strip()},
            ),
            CardActionDTO(
                id="act_dismiss",
                label="Dismiss",
                action_type="dismiss",
                verb="Dismiss",
                payload={},
            ),
        ]

    service = CardsService(session=context.session, settings=get_settings())
    card = await service.upsert_card_for_thread(
        CardCreateDTO(
            user_id=context.user_id,
            thread_id=args.thread_id,
            type=args.type,
            urgency=args.urgency,
            proactivity_tag=args.proactivity_tag,
            verb=args.verb,
            title=args.title,
            summary=args.summary,
            due_at=due_dt,
            actions=actions,
        )
    )
    return ToolResult.success(
        {
            "card_id": str(card.id),
            "title": card.title,
            "verb": card.verb,
            "urgency": card.urgency,
            "status": card.status,
        }
    )


CARD_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="raise_card",
        description=(
            "Create or update a proactive Hark Pro action card on the user's dashboard "
            "with an active verb (Draft, Reply, Accept, Review, Pay) and one-tap CTAs."
        ),
        level="local",
        args_model=RaiseCardArgs,
        handler=raise_card,
    ),
)

__all__ = ["CARD_TOOLS", "RaiseCardActionArgs", "RaiseCardArgs", "raise_card"]
