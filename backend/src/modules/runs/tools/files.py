"""File tools: ``read``, ``write`` and ``edit`` inside the sandbox workspace.

Each handler calls only the sandbox runner protocol. The runner resolves the path below
``/workspace`` and rejects a path outside it. The handlers hold no state between calls.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.modules.runs.tools import Tool, ToolContext, ToolResult


class ReadArgs(BaseModel):
    """Arguments for the ``read`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to read. The path resolves inside /workspace.",
    )


class WriteArgs(BaseModel):
    """Arguments for the ``write`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to write. The path resolves inside /workspace.",
    )
    content: str = Field(..., description="Full UTF-8 text of the file.")


class EditArgs(BaseModel):
    """Arguments for the ``edit`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to edit. The path resolves inside /workspace.",
    )
    old_string: str = Field(
        ...,
        min_length=1,
        description="Exact text to replace. It must occur exactly once.",
    )
    new_string: str = Field(..., description="Replacement text.")


def _decode(payload: bytes) -> str:
    """Decode file bytes without raising on invalid UTF-8."""
    return payload.decode("utf-8", errors="replace")


async def read(args: ReadArgs, context: ToolContext) -> ToolResult:
    """Read one file and return its text."""
    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")
    if isinstance(payload, str):
        return ToolResult.success(payload)
    return ToolResult.success(_decode(payload))


async def write(args: WriteArgs, context: ToolContext) -> ToolResult:
    """Write one file with the full supplied text."""
    data = args.content.encode("utf-8")
    await context.runner.write_file(context.container_id, args.path, data)
    return ToolResult.success(f"wrote {len(data)} bytes to {args.path}")


async def edit(args: EditArgs, context: ToolContext) -> ToolResult:
    """Replace one unique string in a file.

    The tool fails when the old string is absent or when it occurs more than once. It
    never guesses a location. A failed edit does not write the file.
    """
    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")
    original = payload if isinstance(payload, str) else _decode(payload)

    occurrences = original.count(args.old_string)
    if occurrences == 0:
        return ToolResult.failure(
            f"old_string is absent from {args.path}. No edit was applied."
        )
    if occurrences > 1:
        return ToolResult.failure(
            f"old_string appears {occurrences} times in {args.path}. "
            "The edit is ambiguous. Pass a longer old_string that occurs once."
        )

    updated = original.replace(args.old_string, args.new_string, 1)
    await context.runner.write_file(
        context.container_id, args.path, updated.encode("utf-8")
    )
    return ToolResult.success(f"edited {args.path}: replaced 1 occurrence")


FILE_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="read",
        description="Read a text file from the workspace.",
        level="local",
        args_model=ReadArgs,
        handler=read,
    ),
    Tool.define(
        name="write",
        description="Write a full text file to the workspace.",
        level="local",
        args_model=WriteArgs,
        handler=write,
    ),
    Tool.define(
        name="edit",
        description=(
            "Replace one unique string in a workspace file. "
            "The tool fails when the string is absent or ambiguous."
        ),
        level="local",
        args_model=EditArgs,
        handler=edit,
    ),
)


__all__ = [
    "FILE_TOOLS",
    "EditArgs",
    "ReadArgs",
    "WriteArgs",
    "edit",
    "read",
    "write",
]
