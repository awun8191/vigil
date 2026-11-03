"""The ``skill_load`` tool: copy one installed skill into the sandbox workspace.

The tool is a ``local`` tool. It resolves the skill name against the configured
skills root and rejects any path that escapes that root. It then writes each
file below ``/workspace/skills/<name>`` through the sandbox runner. The runner
resolves the container path and rejects an escape a second time.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.config.exceptions import AppException
from src.config.settings import get_settings
from src.modules.runs.tools import Tool, ToolContext, ToolResult
from src.modules.skills.service import (
    collect_skill_files,
    locate_skill_dir,
    resolve_skill_dir,
    write_skill_files,
)


class SkillLoadArgs(BaseModel):
    """Arguments for the ``skill_load`` tool."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description="Name of an installed skill. One path segment.",
    )


async def skill_load(args: SkillLoadArgs, context: ToolContext) -> ToolResult:
    """Copy the named skill into ``/workspace/skills/<name>``.

    A missing skill, an unsafe name, or a file that escapes the skill directory
    returns a failed result. The tool does not raise across the tool boundary.
    """
    try:
        skill_dir = locate_skill_dir(get_settings().SKILLS_ROOT, args.name)
        files = await asyncio.to_thread(collect_skill_files, skill_dir)
        written = await write_skill_files(
            files, args.name, context.runner, context.container_id
        )
    except AppException as exc:
        return ToolResult.failure(exc.message)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"skill not found: {args.name}: {exc}")
    return ToolResult.success(written)


SKILL_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="skill_load",
        description=(
            "Copy an installed skill into /workspace/skills/<name>. "
            "Returns the file names that the copy wrote."
        ),
        level="local",
        args_model=SkillLoadArgs,
        handler=skill_load,
    ),
)


__all__ = ["SKILL_TOOLS", "SkillLoadArgs", "skill_load"]
