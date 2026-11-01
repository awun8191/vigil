"""Human tool: ``ask_user``.

The tool returns a pending marker. It does not block and it does not wait for an answer.
The run pauses on the approval path, which V-07 owns.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.modules.runs.tools import Tool, ToolContext, ToolResult

#: Status value of an unanswered question.
PENDING_STATUS = "pending_user_input"


class AskUserArgs(BaseModel):
    """Arguments for the ``ask_user`` tool."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        ...,
        min_length=1,
        description="The question to show to the user.",
    )
    options: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Optional preset answers. An empty list means free text.",
    )
    context: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional background for the question.",
    )


async def ask_user(args: AskUserArgs, context: ToolContext) -> ToolResult:
    """Return a pending marker for the question."""
    return ToolResult.success(
        {
            "status": PENDING_STATUS,
            "question": args.question,
            "options": list(args.options),
            "context": args.context,
        }
    )


HUMAN_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="ask_user",
        description=(
            "Ask the user a question. The tool returns a pending marker and does not "
            "block. This tool is external and always needs approval."
        ),
        level="external",
        args_model=AskUserArgs,
        handler=ask_user,
    ),
)


__all__ = ["HUMAN_TOOLS", "PENDING_STATUS", "AskUserArgs", "ask_user"]
