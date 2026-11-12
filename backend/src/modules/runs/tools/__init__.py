"""Core tool harness: stateless tools over the sandbox runner and the model clients.

Every tool declares a name, a JSON schema, and an approval level. The level is ``read``,
``local`` or ``external``. A call validates its arguments against the tool schema first.
Only then does the handler run. The handler calls the sandbox runner protocol or it
writes a file to the delivery directory.

A call returns a :class:`ToolResult` with ``{ok, content, artifact_ref, error}``. A tool
never raises across this boundary. Approval logic is not here. V-07 owns the approval
gate. ``ask_user`` is the only ``external`` tool. It returns a pending marker and does
not block.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.integrations.repositories import SandboxRunnerProtocol

ApprovalLevel = Literal["read", "local", "external"]

#: Every approval level that a tool can declare.
APPROVAL_LEVELS: tuple[ApprovalLevel, ...] = ("read", "local", "external")

#: The harness exposes built-in tools.
TOOL_COUNT = 18


class ToolResult(BaseModel):
    """The structured result of one tool call.

    The object has the four keys of the tool call contract. ``content`` holds the tool
    output. ``artifact_ref`` holds a host path when the tool produced a file. ``error``
    holds a message when ``ok`` is false.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool = Field(..., description="True when the call succeeded.")
    content: Any = Field(
        default=None,
        description="Tool output. A string, a dictionary, or null.",
    )
    artifact_ref: str | None = Field(
        default=None,
        description="Host path of a produced file, or null.",
    )
    error: str | None = Field(
        default=None,
        description="Failure message when ok is false, else null.",
    )

    @classmethod
    def success(cls, content: Any = None, *, artifact_ref: str | None = None) -> "ToolResult":
        """Build a successful result."""
        return cls(ok=True, content=content, artifact_ref=artifact_ref, error=None)

    @classmethod
    def failure(cls, error: str, *, content: Any = None) -> "ToolResult":
        """Build a failed result."""
        return cls(ok=False, content=content, artifact_ref=None, error=error)


@dataclass(frozen=True)
class ToolContext:
    """The runner and the delivery directory for one tool call.

    The context holds no state. The caller builds it for each call.
    """

    runner: SandboxRunnerProtocol
    container_id: str
    delivery_dir: Path
    llm: Any | None = None
    """Model client for tools that need inference, such as ``view_image``."""
    secrets: Mapping[str, str] = field(default_factory=dict)
    """Vault secrets resolved for the run owner, keyed by environment name."""
    run_id: UUID | None = None
    """Run that owns this call. Never set from tool args, only by the executor."""
    user_id: UUID | None = None
    """User that owns the run. Never set from tool args, only by the executor."""
    session: Any | None = None
    """Database session for tools that persist rows. Set by the executor."""


Handler = Callable[[Any, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    """One tool: a name, a description, a JSON schema, a level, and a handler."""

    name: str
    description: str
    level: ApprovalLevel
    args_model: type[BaseModel]
    handler: Handler
    input_schema: dict[str, Any]

    @classmethod
    def define(
        cls,
        *,
        name: str,
        description: str,
        level: ApprovalLevel,
        args_model: type[BaseModel],
        handler: Handler,
    ) -> "Tool":
        """Build a tool and derive its JSON schema from the argument model."""
        if not name or not name.strip():
            raise ValueError("a tool name must not be empty")
        if level not in APPROVAL_LEVELS:
            raise ValueError(f"unknown approval level: {level!r}")
        if not issubclass(args_model, BaseModel):
            raise TypeError("args_model must be a pydantic BaseModel subclass")
        return cls(
            name=name,
            description=description,
            level=level,
            args_model=args_model,
            handler=handler,
            input_schema=args_model.model_json_schema(),
        )

    def validate(self, arguments: Mapping[str, Any]) -> BaseModel:
        """Validate the arguments against the tool schema.

        The method raises ``TypeError`` for a non mapping and ``ValidationError`` for a
        bad value. It does not touch the runner.
        """
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be a mapping")
        return self.args_model.model_validate(dict(arguments))

    async def invoke(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        """Validate the arguments, then run the handler. It never raises."""
        try:
            parsed = self.validate(arguments)
        except (ValidationError, TypeError, ValueError) as exc:
            return ToolResult.failure(f"invalid arguments for tool {self.name!r}: {exc}")
        try:
            outcome = self.handler(parsed, context)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except Exception as exc:  # noqa: BLE001 - the boundary must not raise
            return ToolResult.failure(
                f"tool {self.name!r} failed: {type(exc).__name__}: {exc}"
            )
        if not isinstance(outcome, ToolResult):
            return ToolResult.failure(
                f"tool {self.name!r} returned {type(outcome).__name__}, not ToolResult"
            )
        return outcome


# The tool modules import the shared types above, so they load after the definitions.
from src.modules.runs.tools.browser import BROWSER_TOOLS  # noqa: E402
from src.modules.runs.tools.delivery import DELIVERY_TOOLS  # noqa: E402
from src.modules.runs.tools.files import FILE_TOOLS  # noqa: E402
from src.modules.runs.tools.human import HUMAN_TOOLS  # noqa: E402
from src.modules.runs.tools.search import EXA_TOOLS  # noqa: E402
from src.modules.runs.tools.shell import SHELL_TOOLS  # noqa: E402
from src.modules.runs.tools.skills import SKILL_TOOLS  # noqa: E402
from src.modules.runs.tools.tasks import TASK_TOOLS  # noqa: E402
from src.modules.runs.tools.widgets import WIDGET_TOOLS  # noqa: E402


def build_registry(tools: Sequence[Tool]) -> Mapping[str, Tool]:
    """Index tools by name. A duplicate name is an error."""
    registry: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in registry:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        registry[tool.name] = tool
    return MappingProxyType(registry)


#: The core tools of the harness, indexed by name. It is read only.
TOOLS: Mapping[str, Tool] = build_registry(
    (
        *FILE_TOOLS,
        *SHELL_TOOLS,
        *DELIVERY_TOOLS,
        *HUMAN_TOOLS,
        *TASK_TOOLS,
        *SKILL_TOOLS,
        *EXA_TOOLS,
        *BROWSER_TOOLS,
        *WIDGET_TOOLS,
    )
)



def get_tool(name: str) -> Tool:
    """Return the tool with this name. A missing name raises ``KeyError``."""
    try:
        return TOOLS[name]
    except KeyError:
        raise KeyError(f"unknown tool: {name!r}") from None


async def invoke_tool(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> ToolResult:
    """Call a tool by name. An unknown name returns a failed result."""
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult.failure(f"unknown tool: {name!r}")
    return await tool.invoke(arguments, context)


def openai_tool_declarations(names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Return tools in the OpenAI `tools` wire shape.

    The provider expects `{"type": "function", "function": {...}}`. Passing the
    internal declaration shape straight through makes the model believe it has
    no tools at all, and it answers "I cannot run commands".

    Args:
        names: Tool names to include. ``None`` includes the whole registry.

    Returns:
        One declaration per tool, ready for the request payload.
    """
    selected = (
        list(names) if names is not None
        else list(TOOLS.keys())
    )
    declarations: list[dict[str, Any]] = []
    for name in selected:
        tool = TOOLS.get(name)
        if tool is None:
            continue
        declarations.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
        )
    return declarations


def tool_definitions() -> list[dict[str, Any]]:
    """Return the tool declarations for the model prompt."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            "level": tool.level,
        }
        for tool in TOOLS.values()
    ]


__all__ = [
    "APPROVAL_LEVELS",
    "ApprovalLevel",
    "Handler",
    "TOOLS",
    "TOOL_COUNT",
    "Tool",
    "ToolContext",
    "ToolResult",
    "build_registry",
    "get_tool",
    "openai_tool_declarations",
    "invoke_tool",
    "tool_definitions",
]
