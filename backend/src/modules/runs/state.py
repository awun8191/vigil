"""Run state machine and the durable journal that the event loop reads.

The loop holds no state between stages. Every fact that it needs comes from the
journal: the run row, the messages, the approvals and the jobs. A service is a
thin object, so a killed worker loses nothing. A new worker builds a new service
over the same rows and continues.

Two journals implement :class:`RunJournal`:

``InMemoryRunJournal``
    A test double. It holds rows in dictionaries. The same object can serve two
    service instances, so a test can simulate a restart without a database.

``SqlRunJournal``
    The production journal. It reads and writes ``runs``, ``messages``,
    ``approvals``, ``jobs`` and ``agent_configs``. The schema has no step table,
    so step events stay in a process-local buffer. Steps are telemetry only, and
    the loop never reads a step back to decide what to do.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select

from src.config.exceptions import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.approvals.repository import ApprovalDTO
from src.modules.jobs.repository import JobDTO
from src.modules.jobs.service import priority_for_job_type
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    MessageDTO,
    RunCreateDTO,
    RunDTO,
    RunStepCreateDTO,
    RunStepDTO,
)
from src.schemas.runtime import (
    AgentConfigModel,
    ApprovalModel,
    MessageModel,
    RunModel,
    RunStepModel,
)
from src.schemas.system import JobModel

__all__ = [
    "AWAITING_RUN_STATUSES",
    "ApproveDecision",
    "InMemoryRunJournal",
    "InvalidRunTransition",
    "MaxDepthExceeded",
    "PartitionPolicy",
    "RUN_STATUSES",
    "RUN_TRANSITIONS",
    "RunJournal",
    "RunStateMachine",
    "RunStatus",
    "SqlRunJournal",
    "TERMINAL_RUN_STATUSES",
    "TOOL_PARTITIONS",
    "ToolCall",
    "ToolPartition",
]


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC value."""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime. A naive value is read as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Run states. Decision 1 fixes this list. There is no ``stale_thread_paused``.
# ---------------------------------------------------------------------------

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "awaiting_background_job",
    "completed",
    "failed",
]

RUN_STATUSES: tuple[RunStatus, ...] = (
    "queued",
    "running",
    "awaiting_approval",
    "awaiting_background_job",
    "completed",
    "failed",
)

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"completed", "failed"})
"""A run in one of these states never changes again."""

AWAITING_RUN_STATUSES: frozenset[str] = frozenset(
    {"awaiting_approval", "awaiting_background_job"}
)
"""A run in one of these states waits for a row that another actor writes."""

RUN_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "queued": frozenset({"running", "failed"}),
    "running": frozenset(
        {
            "running",
            "awaiting_approval",
            "awaiting_background_job",
            "completed",
            "failed",
        }
    ),
    "awaiting_approval": frozenset({"running", "failed"}),
    "awaiting_background_job": frozenset({"running", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}
"""The allowed status edges. A terminal state has no edge."""


class InvalidRunTransition(RuntimeError):
    """Raised when a change breaks the run state machine."""

    def __init__(self, source: str, target: str, run_id: UUID | None = None) -> None:
        self.source = source
        self.target = target
        self.run_id = run_id
        where = f" for run {run_id}" if run_id else ""
        super().__init__(f"run status cannot change from {source!r} to {target!r}{where}")


class MaxDepthExceeded(ValueError):
    """Raised when a child run would nest deeper than the allowed maximum."""

    def __init__(self, depth: int, max_depth: int) -> None:
        self.depth = depth
        self.max_depth = max_depth
        super().__init__(
            f"child run depth {depth} exceeds MAX_RUN_DEPTH {max_depth}: "
            "a run at the maximum depth cannot spawn a child"
        )


class RunStateMachine:
    """The run state machine. The methods are pure and hold no state."""

    transitions: Mapping[str, frozenset[str]] = RUN_TRANSITIONS

    @classmethod
    def can_transition(cls, source: str, target: str) -> bool:
        """Return True when the edge from source to target exists."""
        return target in cls.transitions.get(source, frozenset())

    @classmethod
    def require(cls, source: str, target: str, run_id: UUID | None = None) -> str:
        """
        Return the target when the edge exists.

        Args:
            source: Current status.
            target: Requested status.
            run_id: Optional id for the error message.

        Returns:
            The target status.

        Raises:
            InvalidRunTransition: The edge does not exist.
        """
        if not cls.can_transition(source, target):
            raise InvalidRunTransition(source, target, run_id)
        return target

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """Return True when the status never changes again."""
        return status in TERMINAL_RUN_STATUSES

    @classmethod
    def is_awaiting(cls, status: str) -> bool:
        """Return True when the status waits for another actor."""
        return status in AWAITING_RUN_STATUSES


# ---------------------------------------------------------------------------
# Tool call partitioning. Decision: local runs here, background runs as a job,
# external needs a human approval row. A tool that the config does not declare
# is external, so an unknown tool can never run without a decision.
# ---------------------------------------------------------------------------

ToolPartition = Literal["local", "background", "external"]

TOOL_PARTITIONS: tuple[ToolPartition, ...] = ("local", "background", "external")


@dataclass(frozen=True)
class ToolCall:
    """One tool call of an assistant message, in a provider neutral shape."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "ToolCall":
        """
        Build a call from either wire shape.

        The loop persists the OpenAI shape ``{"id", "type", "function": {...}}``.
        A test or a provider adapter can also give ``{"id", "name", "arguments"}``.

        Args:
            payload: One entry of an assistant message tool call list.

        Returns:
            The normalized call.

        Raises:
            ValueError: The id or the name is absent.
        """
        function = payload.get("function")
        source: Mapping[str, Any] = function if isinstance(function, Mapping) else payload
        call_id = payload.get("id")
        name = source.get("name")
        if not call_id or not name:
            raise ValueError(f"a tool call needs an id and a name: {payload!r}")
        raw = source.get("arguments")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                raw = {"_raw": raw}
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError(f"tool arguments must be an object: {raw!r}")
        return cls(id=str(call_id), name=str(name), arguments=dict(raw))

    def to_wire(self) -> dict[str, Any]:
        """Return the OpenAI shape that the messages table stores."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass(frozen=True)
class PartitionPolicy:
    """Resolve the partition of a tool call from the agent configuration."""

    levels: Mapping[str, str] = field(default_factory=dict)
    local_tools: frozenset[str] = frozenset()
    default: ToolPartition = "external"

    @classmethod
    def from_config(cls, config: AgentConfigDTO) -> "PartitionPolicy":
        """
        Build the policy from one agent configuration.

        ``approval_policy`` maps a tool name to one of the three partitions. A
        tool in ``tools`` that has no explicit level is local. Any other tool is
        external, so it needs an approval row.

        The mapping fails closed on the value. A level the code does not know
        raises, because the alternative is a silent drop that lets an external
        tool run without a decision. A key may name a tool outside ``tools``,
        since an explicit level is still an explicit decision.

        Raises:
            ValidationError: The policy gives a tool an unknown partition.
        """
        declared = frozenset(config.tools or ())
        levels: dict[str, str] = {}
        for name, level in (config.approval_policy or {}).items():
            tool_name = str(name)
            partition = str(level)
            if partition not in TOOL_PARTITIONS:
                raise ValidationError(
                    f"approval_policy gives {tool_name!r} the unknown partition "
                    f"{partition!r}. Use one of {sorted(TOOL_PARTITIONS)}"
                )
            levels[tool_name] = partition
        return cls(levels=levels, local_tools=declared)

    def partition_for(self, tool_name: str) -> ToolPartition:
        """
        Return the partition of one tool name.

        Args:
            tool_name: Name from the tool call.

        Returns:
            ``local``, ``background`` or ``external``.
        """
        level = self.levels.get(tool_name)
        if level in TOOL_PARTITIONS:
            return level  # type: ignore[return-value]
        if tool_name in self.local_tools:
            return "local"
        return self.default


ApproveDecision = Literal["approved", "rejected"]


# ---------------------------------------------------------------------------
# The journal contract. One object that owns every durable row of a run.
# ---------------------------------------------------------------------------


@runtime_checkable
class RunJournal(Protocol):
    """Storage contract for runs, messages, steps, approvals, jobs and configs."""

    async def get_run(self, run_id: UUID) -> RunDTO | None:
        """Return one run or None."""
        ...

    async def get_latest_run_for_session(self, session_id: UUID) -> RunDTO | None:
        """Return the newest run that owns at least one message in the session."""
        ...

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        """Insert one run row and return it."""
        ...

    async def update_run(
        self,
        run_id: UUID,
        *,
        status: str | None = None,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        """Write the given run fields and return the new row."""
        ...

    async def list_messages(self, run_id: UUID) -> list[MessageDTO]:
        """Return the messages of a run in chronological order."""
        ...

    async def append_message(self, data: MessageCreateDTO) -> MessageDTO:
        """Insert one message row and return it."""
        ...

    async def list_steps(self, run_id: UUID) -> list[RunStepDTO]:
        """Return the recorded steps of a run in order."""
        ...

    async def append_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        """Record one step event."""
        ...

    async def checkpoint(self) -> None:
        """Make the steps written so far visible to a reader on another session.

        The step journal is a live feed: the SSE endpoint reads it from its own
        request session while the run is still executing. A store no longer
        commits, so the loop asks for this checkpoint after each step write.
        It is one commit per batch, not one per token.

        A terminal failure also relies on this call: the worker rolls back the
        job's transaction when a handler raises, so ``_fail_run`` checkpoints the
        failed status and the ``run_failed`` step before it re-raises.
        """
        ...

    async def get_config(self, config_id: UUID) -> AgentConfigDTO | None:
        """Return one agent configuration or None."""
        ...

    async def get_config_by_name(self, name: str) -> AgentConfigDTO | None:
        """Return one agent configuration by its unique name or None."""
        ...

    async def save_config(self, config: AgentConfigDTO) -> AgentConfigDTO:
        """Insert or replace one agent configuration."""
        ...

    async def request_approval(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalDTO:
        """Insert one pending approval row. A repeat call returns the same row."""
        ...

    async def list_approvals(self, run_id: UUID) -> list[ApprovalDTO]:
        """Return the approval rows of a run."""
        ...

    async def decide_approval(
        self,
        approval_id: UUID,
        *,
        status: ApproveDecision,
        note: str | None = None,
        decided_at: datetime | None = None,
    ) -> ApprovalDTO:
        """Write a decision on one approval row and return it."""
        ...

    async def enqueue_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> JobDTO:
        """Insert one pending job row and return it."""
        ...

    async def list_jobs(self, run_id: UUID) -> list[JobDTO]:
        """Return the job rows that belong to a run."""
        ...

    async def complete_job(self, job_id: UUID, result: Mapping[str, Any]) -> JobDTO:
        """Mark one job completed and store its result payload."""
        ...

    async def list_child_runs(self, parent_run_id: UUID) -> list[RunDTO]:
        """Return the direct children of one run."""
        ...

    async def get_cost_micros(self, run_id: UUID) -> int:
        """Return the recorded cost of one run in micro-USD."""
        ...

    async def add_cost_micros(self, run_id: UUID, amount_micros: int) -> int:
        """Charge one run and return its new total in micro-USD."""
        ...

    async def get_workspace_ref(self, run_id: UUID) -> str | None:
        """Return the workspace ref of one run, or None when it has none."""
        ...

    async def set_workspace_ref(self, run_id: UUID, workspace_ref: str) -> None:
        """Assign the workspace ref of one run."""
        ...


# ---------------------------------------------------------------------------
# In-memory journal: the test double.
# ---------------------------------------------------------------------------


class InMemoryRunJournal:
    """A journal that keeps rows in dictionaries. Use it in tests only."""

    def __init__(self) -> None:
        self.runs: dict[UUID, RunDTO] = {}
        self.messages: dict[UUID, list[MessageDTO]] = {}
        self.steps: dict[UUID, list[RunStepDTO]] = {}
        self.configs: dict[UUID, AgentConfigDTO] = {}
        self.approvals: dict[UUID, ApprovalDTO] = {}
        self.jobs: dict[UUID, JobDTO] = {}
        # Sub-agent bookkeeping. Money stays an integer of micro-USD, and
        # each run owns a workspace ref that a child never shares.
        self.cost_micros: dict[UUID, int] = {}
        self.workspaces: dict[UUID, str] = {}

    async def get_run(self, run_id: UUID) -> RunDTO | None:
        return self.runs.get(run_id)

    async def get_latest_run_for_session(self, session_id: UUID) -> RunDTO | None:
        """Return the newest run whose messages carry the session id."""
        latest: tuple[RunDTO, str] | None = None
        for run_id, rows in self.messages.items():
            stamps = [row.created_at for row in rows if row.session_id == session_id]
            if not stamps:
                continue
            run = self.runs.get(run_id)
            if run is None:
                continue
            stamp = max(stamps)
            key = stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)
            if latest is None or key > (latest[1] if latest else ""):
                latest = (run, key)
        return latest[0] if latest else None

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        now = _utc_now()
        run = RunDTO(
            id=uuid4(),
            config_id=data.config_id,
            card_id=data.card_id,
            parent_run_id=data.parent_run_id,
            depth=data.depth,
            status=data.status,
            started_at=now,
            ended_at=None,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            cost_usd=0.0,
            cost_micros=0,
        )
        self.runs[run.id] = run
        self.messages[run.id] = []
        self.steps[run.id] = []
        return run

    async def update_run(
        self,
        run_id: UUID,
        *,
        status: str | None = None,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        if status is not None and run.status in TERMINAL_RUN_STATUSES and status != run.status:
            raise InvalidRunTransition(run.status, status, run_id)
        now = _utc_now()
        new_status = status if status is not None else run.status
        done = new_status in TERMINAL_RUN_STATUSES
        updated = RunDTO(
            id=run.id,
            config_id=run.config_id,
            card_id=run.card_id,
            parent_run_id=run.parent_run_id,
            depth=run.depth,
            status=new_status,
            started_at=run.started_at,
            ended_at=ended_at if ended_at is not None else (now if done else run.ended_at),
            token_usage=token_usage if token_usage is not None else run.token_usage,
            cost_usd=cost_usd if cost_usd is not None else run.cost_usd,
            cost_micros=self.cost_micros.get(run_id, run.cost_micros),
        )
        self.runs[run_id] = updated
        return updated

    async def list_child_runs(self, parent_run_id: UUID) -> list[RunDTO]:
        """Return the direct children of one run."""
        return [
            run for run in self.runs.values() if run.parent_run_id == parent_run_id
        ]

    async def get_cost_micros(self, run_id: UUID) -> int:
        """Return the recorded cost of one run in micro-USD."""
        return self.cost_micros.get(run_id, 0)

    async def add_cost_micros(self, run_id: UUID, amount_micros: int) -> int:
        """Charge one run and return its new total in micro-USD."""
        if run_id not in self.runs:
            raise KeyError(f"run {run_id} is absent from the journal")
        total = self.cost_micros.get(run_id, 0) + amount_micros
        self.cost_micros[run_id] = total
        run = self.runs[run_id]
        self.runs[run_id] = RunDTO(
            id=run.id,
            config_id=run.config_id,
            card_id=run.card_id,
            parent_run_id=run.parent_run_id,
            depth=run.depth,
            status=run.status,
            started_at=run.started_at,
            ended_at=run.ended_at,
            token_usage=run.token_usage,
            cost_usd=total / 1_000_000,
            cost_micros=total,
        )
        return total

    async def get_workspace_ref(self, run_id: UUID) -> str | None:
        """Return the workspace ref of one run, or None when it has none."""
        return self.workspaces.get(run_id)

    async def set_workspace_ref(self, run_id: UUID, workspace_ref: str) -> None:
        """Assign the workspace ref of one run."""
        if run_id not in self.runs:
            raise KeyError(f"run {run_id} is absent from the journal")
        self.workspaces[run_id] = workspace_ref

    async def list_messages(self, run_id: UUID) -> list[MessageDTO]:
        return list(self.messages.get(run_id, ()))

    async def list_session_history(
        self,
        session_id: UUID,
        exclude_run_id: UUID | None = None,
        limit: int = 40,
    ) -> list[MessageDTO]:
        """Return prior user and final assistant messages of a chat session."""
        out: list[MessageDTO] = []
        for r_id, msgs in self.messages.items():
            if exclude_run_id is not None and r_id == exclude_run_id:
                continue
            for m in msgs:
                if m.session_id != session_id:
                    continue
                if m.role == "user" or (
                    m.role == "assistant" and not m.tool_calls and m.content
                ):
                    out.append(m)
        out.sort(key=lambda m: m.created_at)
        return out[-limit:]

    async def replace_messages(self, run_id: UUID, messages: list[MessageDTO]) -> None:
        self.messages[run_id] = list(messages)

    async def append_message(self, data: MessageCreateDTO) -> MessageDTO:
        message = MessageDTO(
            id=uuid4(),
            run_id=data.run_id,
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            tool_calls=data.tool_calls,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            tool_arguments=data.tool_arguments,
            tool_result_reference=data.tool_result_reference,
            created_at=_utc_now(),
        )
        if data.run_id is not None:
            self.messages.setdefault(data.run_id, []).append(message)
        return message

    async def list_steps(self, run_id: UUID) -> list[RunStepDTO]:
        return list(self.steps.get(run_id, ()))

    async def append_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        steps = self.steps.setdefault(data.run_id, [])
        # Mirror SqlRunJournal: the stored number is the greater of the request
        # and one above the highest number already stored, so a caller may pass
        # 0 to let the journal allocate. Counting the rows instead would hand out
        # a number below an existing one as soon as the history has a gap, and a
        # decreasing number is dropped by the step stream.
        highest = max((step.step_number for step in steps), default=0)
        step_number = max(data.step_number, highest + 1)
        step = RunStepDTO(
            id=uuid4(),
            run_id=data.run_id,
            step_number=step_number,
            event_type=data.event_type,
            payload=data.payload,
            created_at=_utc_now(),
        )
        steps.append(step)
        return step

    async def checkpoint(self) -> None:
        """No-op: the in-memory journal has no transaction to close."""
        return None

    async def get_config(self, config_id: UUID) -> AgentConfigDTO | None:
        return self.configs.get(config_id)

    async def get_config_by_name(self, name: str) -> AgentConfigDTO | None:
        for config in self.configs.values():
            if config.name == name:
                return config
        return None

    async def save_config(self, config: AgentConfigDTO) -> AgentConfigDTO:
        self.configs[config.id] = config
        return config

    async def request_approval(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalDTO:
        for row in self.approvals.values():
            if row.run_id == run_id and row.tool_call_id == tool_call_id:
                return row
        approval = ApprovalDTO(
            id=uuid4(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            status="pending",
            decided_at=None,
            decision_note=None,
            created_at=_utc_now(),
        )
        self.approvals[approval.id] = approval
        return approval

    async def list_approvals(self, run_id: UUID) -> list[ApprovalDTO]:
        return [row for row in self.approvals.values() if row.run_id == run_id]

    async def decide_approval(
        self,
        approval_id: UUID,
        *,
        status: ApproveDecision,
        note: str | None = None,
        decided_at: datetime | None = None,
    ) -> ApprovalDTO:
        row = self.approvals[approval_id]
        updated = ApprovalDTO(
            id=row.id,
            run_id=row.run_id,
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            arguments=row.arguments,
            status=status,
            decided_at=decided_at or _utc_now(),
            decision_note=note,
            created_at=row.created_at,
        )
        self.approvals[approval_id] = updated
        return updated

    async def enqueue_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> JobDTO:
        now = _utc_now()
        job = JobDTO(
            id=uuid4(),
            type=job_type,
            payload=dict(payload),
            run_at=now,
            attempts=0,
            max_attempts=max_attempts,
            status="pending",
            locked_by=None,
            locked_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
            priority=priority_for_job_type(job_type),
            heartbeat_at=None,
        )
        self.jobs[job.id] = job
        return job

    async def list_jobs(self, run_id: UUID) -> list[JobDTO]:
        return [job for job in self.jobs.values() if job.payload.get("run_id") == str(run_id)]

    async def complete_job(self, job_id: UUID, result: Mapping[str, Any]) -> JobDTO:
        job = self.jobs[job_id]
        updated = replace(
            job,
            status="completed",
            payload={**job.payload, "result": dict(result)},
            locked_by=None,
            updated_at=_utc_now(),
        )
        self.jobs[job_id] = updated
        return updated


# ---------------------------------------------------------------------------
# SQL journal: the production path.
# ---------------------------------------------------------------------------


class SqlRunJournal:
    """A journal over the application database session.

    Every write flushes into the caller's transaction and returns. The boundary
    that owns the session (a request, a worker job, a test) commits, so one unit
    of work lands whole or not at all up to the next checkpoint. ``checkpoint()``
    is the exception: the step feed is read live by the SSE endpoint, so the loop
    commits the steps written so far and a later failure keeps them. Steps are
    rows, so a restart keeps the history and the SSE resume point.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _run_dto(model: RunModel) -> RunDTO:
        return RunDTO(
            id=model.id,
            config_id=model.config_id,
            card_id=model.card_id,
            parent_run_id=model.parent_run_id,
            depth=model.depth,
            status=model.status,
            started_at=_as_utc(model.started_at),
            ended_at=_as_utc(model.ended_at) if model.ended_at is not None else None,
            token_usage=dict(model.token_usage or {}),
            cost_micros=int(model.cost_micros or 0),
            cost_usd=(model.cost_micros or 0) / 1_000_000,
        )

    @staticmethod
    def _step_dto(model: RunStepModel) -> RunStepDTO:
        return RunStepDTO(
            id=model.id,
            run_id=model.run_id,
            step_number=model.step_number,
            event_type=model.event_type,
            payload=dict(model.payload or {}),
            created_at=_as_utc(model.created_at),
        )

    @staticmethod
    def _message_dto(model: MessageModel) -> MessageDTO:
        return MessageDTO(
            id=model.id,
            run_id=model.run_id,
            session_id=model.session_id,
            role=model.role,
            content=model.content,
            tool_calls=model.tool_calls,
            tool_call_id=model.tool_call_id,
            tool_name=model.tool_name,
            tool_arguments=model.tool_arguments,
            tool_result_reference=model.tool_result_reference,
            created_at=_as_utc(model.created_at),
        )

    @staticmethod
    def _approval_dto(model: ApprovalModel) -> ApprovalDTO:
        return ApprovalDTO(
            id=model.id,
            run_id=model.run_id,
            tool_call_id=model.tool_call_id,
            tool_name=model.tool_name,
            arguments=dict(model.arguments or {}),
            status=model.status,
            decided_at=_as_utc(model.decided_at) if model.decided_at else None,
            decision_note=model.decision_note,
            created_at=_as_utc(model.created_at),
        )

    @staticmethod
    def _job_dto(model: JobModel) -> JobDTO:
        return JobDTO(
            id=model.id,
            type=model.type,
            payload=dict(model.payload or {}),
            run_at=_as_utc(model.run_at),
            attempts=model.attempts,
            max_attempts=model.max_attempts,
            status=model.status,
            locked_by=model.locked_by,
            locked_at=_as_utc(model.locked_at) if model.locked_at else None,
            last_error=model.last_error,
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
            priority=model.priority,
            heartbeat_at=_as_utc(model.heartbeat_at) if model.heartbeat_at else None,
        )

    @staticmethod
    def _config_dto(model: AgentConfigModel) -> AgentConfigDTO:
        return AgentConfigDTO(
            id=model.id,
            name=model.name,
            system_prompt=model.system_prompt,
            model=model.model,
            tools=list(model.tools or []),
            skills=list(model.skills or []),
            memory_scopes=list(model.memory_scopes or []),
            approval_policy=dict(model.approval_policy or {}),
            limits=dict(model.limits or {}),
        )

    # -- runs -------------------------------------------------------------

    async def get_run(self, run_id: UUID) -> RunDTO | None:
        try:
            model = await self.session.get(RunModel, run_id, populate_existing=True)
        except TypeError:
            model = await self.session.get(RunModel, run_id)
        return None if model is None else self._run_dto(model)

    async def get_latest_run_for_session(self, session_id: UUID) -> RunDTO | None:
        """Return the newest run that owns at least one message in the session.

        The join rides the unified messages table (runs carry no session
        column); newest means the run with the latest message timestamp.
        """
        latest_stamp = (
            select(func.max(MessageModel.created_at))
            .where(MessageModel.run_id == RunModel.id, MessageModel.session_id == session_id)
            .correlate(RunModel)
            .scalar_subquery()
        )
        exists_stmt = (
            select(MessageModel.id)
            .where(MessageModel.run_id == RunModel.id, MessageModel.session_id == session_id)
            .exists()
        )
        result = await self.session.execute(
            select(RunModel).where(exists_stmt).order_by(latest_stamp.desc()).limit(1)
        )
        model = result.scalars().first()
        return None if model is None else self._run_dto(model)

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        now = _utc_now()
        model = RunModel(
            id=uuid4(),
            config_id=data.config_id,
            card_id=data.card_id,
            parent_run_id=data.parent_run_id,
            depth=data.depth,
            status=data.status,
            started_at=now,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            cost_micros=0,
        )
        self.session.add(model)
        await self.session.flush()
        return self._run_dto(model)

    async def update_run(
        self,
        run_id: UUID,
        *,
        status: str | None = None,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        result = await self.session.execute(
            select(RunModel).where(RunModel.id == run_id).with_for_update()
        )
        model = result.scalars().first()
        if model is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        if status is not None:
            if model.status in TERMINAL_RUN_STATUSES and status != model.status:
                raise InvalidRunTransition(model.status, status, run_id)
            model.status = status
            if status in TERMINAL_RUN_STATUSES:
                model.ended_at = ended_at or _utc_now()
        if ended_at is not None:
            model.ended_at = ended_at
        if token_usage is not None:
            model.token_usage = dict(token_usage)
        if cost_usd is not None:
            model.cost_micros = int(round(cost_usd * 1_000_000))
        await self.session.flush()
        return self._run_dto(model)

    # -- sub-agent tree ---------------------------------------------------

    async def list_child_runs(self, parent_run_id: UUID) -> list[RunDTO]:
        """Return the direct children of one run."""
        result = await self.session.execute(
            select(RunModel)
            .where(RunModel.parent_run_id == parent_run_id)
            .order_by(RunModel.started_at.asc(), RunModel.id.asc())
        )
        return [self._run_dto(row) for row in result.scalars().all()]

    async def get_cost_micros(self, run_id: UUID) -> int:
        """Return the recorded cost of one run in micro-USD."""
        model = await self.session.get(RunModel, run_id)
        if model is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        return model.cost_micros or 0

    async def add_cost_micros(self, run_id: UUID, amount_micros: int) -> int:
        """Charge one run and return its new total in micro-USD."""
        model = await self.session.get(RunModel, run_id)
        if model is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        model.cost_micros = (model.cost_micros or 0) + amount_micros
        await self.session.flush()
        return model.cost_micros

    async def get_workspace_ref(self, run_id: UUID) -> str | None:
        """Return the workspace ref of one run, or None when it has none."""
        model = await self.session.get(RunModel, run_id)
        if model is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        return model.workspace_ref

    async def set_workspace_ref(self, run_id: UUID, workspace_ref: str) -> None:
        """Assign the workspace ref of one run."""
        model = await self.session.get(RunModel, run_id)
        if model is None:
            raise KeyError(f"run {run_id} is absent from the journal")
        model.workspace_ref = workspace_ref
        await self.session.flush()

    # -- messages ---------------------------------------------------------

    async def list_messages(self, run_id: UUID) -> list[MessageDTO]:
        result = await self.session.execute(
            select(MessageModel)
            .where(MessageModel.run_id == run_id)
            .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
        )
        return [self._message_dto(row) for row in result.scalars().all()]

    async def list_session_history(
        self,
        session_id: UUID,
        exclude_run_id: UUID | None = None,
        limit: int = 40,
    ) -> list[MessageDTO]:
        """Return prior user and final assistant messages of a chat session."""
        stmt = select(MessageModel).where(
            MessageModel.session_id == session_id,
            MessageModel.role.in_(("user", "assistant")),
        )
        if exclude_run_id is not None:
            stmt = stmt.where(
                or_(
                    MessageModel.run_id.is_(None),
                    MessageModel.run_id != exclude_run_id,
                )
            )
        stmt = stmt.order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
        result = await self.session.execute(stmt)
        rows = [
            row
            for row in result.scalars().all()
            if row.role == "user" or (row.role == "assistant" and not row.tool_calls and row.content)
        ]
        return [self._message_dto(row) for row in rows[-limit:]]

    async def replace_messages(self, run_id: UUID, messages: list[MessageDTO]) -> None:
        await self.session.execute(
            delete(MessageModel).where(MessageModel.run_id == run_id)
        )
        for msg in messages:
            self.session.add(
                MessageModel(
                    id=msg.id,
                    run_id=run_id,
                    session_id=msg.session_id,
                    role=msg.role,
                    content=msg.content,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                    tool_name=msg.tool_name,
                    tool_arguments=msg.tool_arguments,
                    tool_result_reference=msg.tool_result_reference,
                    created_at=msg.created_at,
                )
            )
        await self.session.flush()

    async def append_message(self, data: MessageCreateDTO) -> MessageDTO:
        model = MessageModel(
            id=uuid4(),
            run_id=data.run_id,
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            tool_calls=data.tool_calls,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            tool_arguments=data.tool_arguments,
            tool_result_reference=data.tool_result_reference,
            created_at=_utc_now(),
        )
        self.session.add(model)
        await self.session.flush()
        return self._message_dto(model)

    # -- steps (durable) ---------------------------------------------------

    async def list_steps(self, run_id: UUID) -> list[RunStepDTO]:
        result = await self.session.execute(
            select(RunStepModel)
            .where(RunStepModel.run_id == run_id)
            .order_by(RunStepModel.step_number.asc())
        )
        return [self._step_dto(model) for model in result.scalars().all()]

    async def append_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        # Lock the parent run row so concurrent writers allocate monotonic step numbers
        await self.session.execute(
            select(RunModel.id).where(RunModel.id == data.run_id).with_for_update()
        )
        max_res = await self.session.execute(
            select(func.coalesce(func.max(RunStepModel.step_number), 0)).where(
                RunStepModel.run_id == data.run_id
            )
        )
        next_step = int(max_res.scalar() or 0) + 1
        step_number = max(data.step_number, next_step)
        model = RunStepModel(
            id=uuid4(),
            run_id=data.run_id,
            step_number=step_number,
            event_type=data.event_type,
            payload=dict(data.payload or {}),
            created_at=_utc_now(),
        )
        self.session.add(model)
        await self.session.flush()
        return self._step_dto(model)

    async def checkpoint(self) -> None:
        """Close the current transaction so a reader on another session sees the steps.

        The SSE endpoint polls this table from its own request session while the
        run is still executing, so a step that sits in an open transaction is
        invisible and the live view stalls. The loop calls this after each step
        write. It is one commit per batch, not one per token.
        """
        await self.session.commit()

    # -- configs ----------------------------------------------------------

    async def get_config(self, config_id: UUID) -> AgentConfigDTO | None:
        model = await self.session.get(AgentConfigModel, config_id)
        return None if model is None else self._config_dto(model)

    async def get_config_by_name(self, name: str) -> AgentConfigDTO | None:
        result = await self.session.execute(
            select(AgentConfigModel).where(AgentConfigModel.name == name)
        )
        model = result.scalars().first()
        return None if model is None else self._config_dto(model)

    async def save_config(self, config: AgentConfigDTO) -> AgentConfigDTO:
        model = await self.session.get(AgentConfigModel, config.id)
        if model is None:
            model = AgentConfigModel(id=config.id)
            self.session.add(model)
        model.name = config.name
        model.system_prompt = config.system_prompt
        model.model = config.model
        model.tools = list(config.tools)
        model.skills = list(config.skills)
        model.memory_scopes = list(config.memory_scopes)
        model.approval_policy = dict(config.approval_policy)
        model.limits = dict(config.limits)
        await self.session.flush()
        return self._config_dto(model)

    # -- approvals --------------------------------------------------------

    async def request_approval(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalDTO:
        result = await self.session.execute(
            select(ApprovalModel).where(
                ApprovalModel.run_id == run_id,
                ApprovalModel.tool_call_id == tool_call_id,
            )
        )
        existing = result.scalars().first()
        if existing is not None:
            return self._approval_dto(existing)
        model = ApprovalModel(
            id=uuid4(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            status="pending",
            created_at=_utc_now(),
        )
        self.session.add(model)
        await self.session.flush()
        return self._approval_dto(model)

    async def list_approvals(self, run_id: UUID) -> list[ApprovalDTO]:
        result = await self.session.execute(
            select(ApprovalModel)
            .where(ApprovalModel.run_id == run_id)
            .order_by(ApprovalModel.created_at.asc(), ApprovalModel.id.asc())
        )
        return [self._approval_dto(row) for row in result.scalars().all()]

    async def decide_approval(
        self,
        approval_id: UUID,
        *,
        status: ApproveDecision,
        note: str | None = None,
        decided_at: datetime | None = None,
    ) -> ApprovalDTO:
        model = await self.session.get(ApprovalModel, approval_id)
        if model is None:
            raise KeyError(f"approval {approval_id} is absent from the journal")
        model.status = status
        model.decided_at = decided_at or _utc_now()
        model.decision_note = note
        await self.session.flush()
        return self._approval_dto(model)

    # -- jobs -------------------------------------------------------------

    async def enqueue_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> JobDTO:
        now = _utc_now()
        model = JobModel(
            id=uuid4(),
            type=job_type,
            payload=dict(payload),
            run_at=now,
            attempts=0,
            max_attempts=max_attempts,
            status="pending",
            priority=priority_for_job_type(job_type),
            created_at=now,
            updated_at=now,
        )
        self.session.add(model)
        await self.session.flush()
        return self._job_dto(model)

    async def list_jobs(self, run_id: UUID) -> list[JobDTO]:
        result = await self.session.execute(
            select(JobModel)
            .where(JobModel.payload["run_id"].astext == str(run_id))
            .order_by(JobModel.created_at.asc(), JobModel.id.asc())
        )
        return [self._job_dto(row) for row in result.scalars().all()]

    async def complete_job(self, job_id: UUID, result: Mapping[str, Any]) -> JobDTO:
        model = await self.session.get(JobModel, job_id)
        if model is None:
            raise KeyError(f"job {job_id} is absent from the journal")
        model.status = "completed"
        model.payload = {**dict(model.payload or {}), "result": dict(result)}
        model.locked_by = None
        model.updated_at = _utc_now()
        await self.session.flush()
        return self._job_dto(model)
