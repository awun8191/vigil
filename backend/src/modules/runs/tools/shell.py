"""Shell tool: ``bash``.

The handler calls only ``SandboxRunnerProtocol.execute_command``. The runner runs argv,
never a shell, and it gives the process no stdin. The tool adds a default 120 second
timeout and a 256 KB cap on the combined output text.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.integrations.sandbox.contract import MAX_STREAM_BYTES, MAX_TIMEOUT_SECONDS
from src.modules.runs.tools import Tool, ToolContext, ToolResult

#: Default time limit of one command.
DEFAULT_TIMEOUT_SECONDS = 120


class BashArgs(BaseModel):
    """Arguments for the ``bash`` tool."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        ...,
        min_length=1,
        description="Command line. The runner splits it into argv and starts no shell.",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_TIMEOUT_SECONDS,
        description="Time limit in seconds. The default is 120.",
    )


def _clip(text: str) -> str:
    """Limit the text to the output cap and mark a cut."""
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_STREAM_BYTES:
        return text
    marker = f"\n[output truncated at {MAX_STREAM_BYTES} bytes]"
    keep = MAX_STREAM_BYTES - len(marker.encode("utf-8"))
    return encoded[:keep].decode("utf-8", errors="ignore") + marker


def _artifact_ref(result: Any) -> str | None:
    """Return the stdout overflow artifact path, else the first artifact path."""
    artifacts = tuple(getattr(result, "artifacts", ()) or ())
    for artifact in artifacts:
        if getattr(artifact, "stream", None) == "stdout":
            return getattr(artifact, "path", None)
    if artifacts:
        return getattr(artifacts[0], "path", None)
    return None


async def bash(args: BashArgs, context: ToolContext) -> ToolResult:
    """Run one command in the sandbox and return its exit code and output."""
    result = await context.runner.execute_command(
        context.container_id,
        args.command,
        timeout_seconds=args.timeout_seconds,
    )
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    exit_code = int(getattr(result, "exit_code", -1))

    body = (
        f"exit_code: {exit_code}\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}"
    )
    content = _clip(body)
    artifact_ref = _artifact_ref(result)
    if exit_code == 0:
        return ToolResult.success(content, artifact_ref=artifact_ref)
    return ToolResult(
        ok=False,
        content=content,
        artifact_ref=artifact_ref,
        error=f"command exited with code {exit_code}",
    )


SHELL_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="bash",
        description=(
            "Run one command in the sandbox. The default timeout is 120 seconds "
            "and the output cap is 256 KB. The command gets no stdin."
        ),
        level="local",
        args_model=BashArgs,
        handler=bash,
    ),
)


__all__ = ["BashArgs", "DEFAULT_TIMEOUT_SECONDS", "SHELL_TOOLS", "bash"]
