"""Task tool: ``manage_tasks``.

The tool is a pure function. The caller passes the current task list and the tool returns
the new list. The tool stores nothing between calls.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.modules.runs.tools import Tool, ToolContext, ToolResult

TaskStatus = Literal["pending", "in_progress", "completed"]

#: The maximum number of tasks in one list.
MAX_TASKS = 50


class TaskItem(BaseModel):
    """One task in the list."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, description="Task identifier.")
    title: str = Field(..., min_length=1, max_length=500, description="Task text.")
    status: TaskStatus = Field(default="pending", description="Task state.")


class ManageTasksArgs(BaseModel):
    """Arguments for the ``manage_tasks`` tool."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["list", "create", "update", "delete"] = Field(
        ...,
        description="The operation to apply to the task list.",
    )
    tasks: list[TaskItem] = Field(
        default_factory=list,
        max_length=MAX_TASKS,
        description="The current task list. The tool does not read a stored list.",
    )
    task_id: str | None = Field(
        default=None,
        max_length=64,
        description="Target task for update and delete.",
    )
    title: str | None = Field(
        default=None,
        max_length=500,
        description="Task text for create, or the new text for update.",
    )
    status: TaskStatus | None = Field(
        default=None,
        description="New state for update.",
    )

    @model_validator(mode="after")
    def _check_operation_fields(self) -> "ManageTasksArgs":
        """Reject a call that lacks the fields of its operation."""
        if self.operation == "create" and not self.title:
            raise ValueError("create needs title")
        if self.operation in ("update", "delete") and not self.task_id:
            raise ValueError(f"{self.operation} needs task_id")
        if self.operation == "update" and self.title is None and self.status is None:
            raise ValueError("update needs title or status")
        return self


def _next_id(tasks: list[TaskItem]) -> str:
    """Return an unused task identifier."""
    used = {task.id for task in tasks}
    index = len(tasks) + 1
    while f"task-{index}" in used:
        index += 1
    return f"task-{index}"


def _as_content(tasks: list[TaskItem]) -> dict[str, object]:
    """Return the task list as tool content."""
    return {"tasks": [task.model_dump() for task in tasks], "count": len(tasks)}


async def manage_tasks(args: ManageTasksArgs, context: ToolContext) -> ToolResult:
    """Apply one operation to the supplied task list."""
    tasks = list(args.tasks)

    if args.operation == "list":
        return ToolResult.success(_as_content(tasks))

    if args.operation == "create":
        identifier = args.task_id or _next_id(tasks)
        if any(task.id == identifier for task in tasks):
            return ToolResult.failure(f"task id already exists: {identifier!r}")
        if len(tasks) >= MAX_TASKS:
            return ToolResult.failure(f"the task list is full: {MAX_TASKS} tasks")
        tasks.append(TaskItem(id=identifier, title=args.title or "", status=args.status or "pending"))
        return ToolResult.success(_as_content(tasks))

    index = next((i for i, task in enumerate(tasks) if task.id == args.task_id), None)
    if index is None:
        return ToolResult.failure(f"task not found: {args.task_id!r}")

    if args.operation == "delete":
        tasks.pop(index)
        return ToolResult.success(_as_content(tasks))

    updated = tasks[index].model_copy(
        update={
            "title": args.title if args.title is not None else tasks[index].title,
            "status": args.status if args.status is not None else tasks[index].status,
        }
    )
    tasks[index] = updated
    return ToolResult.success(_as_content(tasks))


TASK_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="manage_tasks",
        description=(
            "List, create, update or delete tasks. The caller passes the current list "
            "and the tool returns the new list."
        ),
        level="local",
        args_model=ManageTasksArgs,
        handler=manage_tasks,
    ),
)


__all__ = [
    "MAX_TASKS",
    "TASK_TOOLS",
    "ManageTasksArgs",
    "TaskItem",
    "TaskStatus",
    "manage_tasks",
]
