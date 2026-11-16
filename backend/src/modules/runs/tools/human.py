"""Human tool: ``ask_user``.

Supports both a single question and multi-question stacked multiple-choice
clarifications. The tool returns a pending marker and pauses the run on the
approval path until the user submits structured answers.
"""
from __future__ import annotations

from typing import Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.modules.runs.tools import Tool, ToolContext, ToolResult

#: Status value of an unanswered question.
PENDING_STATUS = "pending_user_input"


class QuestionOption(BaseModel):
    """One selectable choice for a question."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class QuestionItem(BaseModel):
    """One question in a multi-question stacked prompt."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=64)
    header: str | None = Field(
        default=None,
        max_length=64,
        description="Short section header, e.g. 'Tone', 'Recipients', 'Scope'.",
    )
    question: str = Field(..., min_length=1, max_length=1000)
    options: list[Union[str, QuestionOption]] = Field(
        default_factory=list,
        max_length=10,
        description="Preset options. Empty list means free-text input.",
    )
    multi_select: bool = Field(
        default=False,
        description="True to allow selecting multiple options.",
    )
    allow_other: bool = Field(
        default=True,
        description="True to include an inline 'Other' custom text input.",
    )


class AskUserArgs(BaseModel):
    """Arguments for the ``ask_user`` tool."""

    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(
        default=None,
        min_length=1,
        description="Single question to show to the user (or use 'questions' for multiple).",
    )
    options: list[Union[str, QuestionOption]] = Field(
        default_factory=list,
        max_length=10,
        description="Optional preset answers for a single question.",
    )
    multi_select: bool = Field(
        default=False,
        description="Allow selecting multiple options for a single question.",
    )
    allow_other: bool = Field(
        default=True,
        description="Allow a custom free-text response alongside preset options.",
    )
    context: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional background or rationale for the question(s).",
    )
    questions: list[QuestionItem] = Field(
        default_factory=list,
        max_length=6,
        description="Stacked list of 1 to 6 multiple-choice or free-text questions.",
    )

    @model_validator(mode="after")
    def _ensure_at_least_one_question(self) -> "AskUserArgs":
        if not self.question and not self.questions:
            raise ValueError("Provide either 'question' or a non-empty 'questions' list.")
        return self


async def ask_user(args: AskUserArgs, context: ToolContext) -> ToolResult:
    """Return a pending marker for one or more structured questions."""
    normalized_questions: list[dict[str, object]] = []
    if args.questions:
        for idx, q in enumerate(args.questions):
            normalized_questions.append(
                {
                    "id": q.id or f"q_{idx + 1}",
                    "header": q.header,
                    "question": q.question,
                    "options": [
                        opt.model_dump() if isinstance(opt, QuestionOption) else {"label": str(opt)}
                        for opt in q.options
                    ],
                    "multi_select": q.multi_select,
                    "allow_other": q.allow_other,
                }
            )
    elif args.question:
        normalized_questions.append(
            {
                "id": "q_1",
                "header": None,
                "question": args.question,
                "options": [
                    opt.model_dump() if isinstance(opt, QuestionOption) else {"label": str(opt)}
                    for opt in args.options
                ],
                "multi_select": args.multi_select,
                "allow_other": args.allow_other,
            }
        )

    return ToolResult.success(
        {
            "status": PENDING_STATUS,
            "question": args.question or (normalized_questions[0]["question"] if normalized_questions else ""),
            "options": [
                opt.model_dump() if isinstance(opt, QuestionOption) else opt
                for opt in args.options
            ],
            "questions": normalized_questions,
            "context": args.context,
        }
    )


HUMAN_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="ask_user",
        description=(
            "Ask the user one or more structured questions (supports stacked multiple-choice "
            "questions via `questions` with `options`, `multi_select`, and `allow_other`). "
            "Use this during brainstorming or before ambiguous actions. The run pauses until "
            "the user submits their answers."
        ),
        level="external",
        args_model=AskUserArgs,
        handler=ask_user,
    ),
)


__all__ = [
    "HUMAN_TOOLS",
    "PENDING_STATUS",
    "AskUserArgs",
    "QuestionItem",
    "QuestionOption",
    "ask_user",
]
