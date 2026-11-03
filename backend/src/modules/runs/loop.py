"""The stateless six-stage event loop.

One iteration runs a fixed pipeline:

1. ``assemble``: read the run, the config and the messages, and build the model
   context with one tool group per assistant turn, in call order.
2. ``compact_check``: estimate the context size and record a compaction request
   when it passes the ratio.
3. ``infer``: assert the barrier, then call the model once.
4. ``partition``: classify each tool call as local, background or external.
5. ``dispatch``: run a local call, enqueue a background job, request an approval.
   The stage is idempotent. It satisfies inherited work from a killed iteration
   before it starts new work.
6. ``record``: write the steps, the token usage and the run status.

Stages one, two, five and six also run when the loop finds an unanswered
assistant turn. That iteration satisfies the turn and returns without a model
call, so the barrier never blocks a bad history into a provider request.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from src.integrations.repositories import LLMClientProtocol, LLMResponseDTO
from src.modules.runs.barrier import assert_run_barrier, tool_call_ids
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    MessageDTO,
    RunDTO,
    RunStepCreateDTO,
)
from src.modules.runs.state import (
    AWAITING_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    PartitionPolicy,
    RunJournal,
    RunStateMachine,
    ToolCall,
    ToolPartition,
)


__all__ = [
    "BACKGROUND_JOB_TYPE",
    "STAGES",
    "AssembledContext",
    "CompactionProvider",
    "DispatchOutcome",
    "LoopDependencyMissing",
    "LoopLimitExceeded",
    "LoopIteration",
    "NullObserver",
    "RunConfigMissing",
    "RunLoop",
    "RunNotFound",
    "Stage",
    "StageObserver",
    "ToolExecutor",
    "USER_REJECTED_RESULT",
    "build_model_messages",
    "estimate_tokens",
]

Stage = Literal[
    "assemble",
    "compact_check",
    "infer",
    "partition",
    "dispatch",
    "record",
]

STAGES: tuple[Stage, ...] = (
    "assemble",
    "compact_check",
    "infer",
    "partition",
    "dispatch",
    "record",
)
"""The six stages, in pipeline order."""

BACKGROUND_JOB_TYPE = "execute_run"
"""Job type for a background tool call.

The queue in this build has no tool-only job type, and ``execute_run`` is the
run-execution type that a worker already claims. The payload carries the tool
call, so the handler runs the tool and then enqueues ``resume_run``.
"""

USER_REJECTED_RESULT: dict[str, str] = {
    "error": "USER_REJECTED",
    "message": "The user rejected this proposed action.",
}
"""The tool result that a rejected approval writes. The model then adapts."""


class LoopDependencyMissing(RuntimeError):
    """Raised when the loop has no model client or no tool executor."""


class LoopLimitExceeded(RuntimeError):
    """Raised when a run does not reach a stop state inside the iteration cap."""


class RunNotFound(LookupError):
    """Raised when the run id has no row."""


class RunConfigMissing(LookupError):
    """Raised when the run names an agent config that has no row."""


@runtime_checkable
class ToolExecutor(Protocol):
    """Run tool calls outside the loop.

    The loop never imports a sandbox, a connector or a container. A test injects
    a fake. V-08 wires the real adapters.
    """

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one local tool and return its structured result."""
        ...

    async def execute_external(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one approved external tool through its connector."""
        ...


@runtime_checkable
class CompactionProvider(Protocol):
    """Replace old messages with a summary when the context is too large."""

    async def compact(self, run_id: UUID, messages: Sequence[MessageDTO]) -> bool:
        """Compact the history. Return True when the history changed."""
        ...


@runtime_checkable
class StageObserver(Protocol):
    """Observe a completed stage. A test uses it to kill the loop."""

    async def on_stage(self, run_id: UUID, stage: Stage) -> None:
        """Handle the end of one stage."""
        ...


class NullObserver:
    """An observer that does nothing. It is the default."""

    async def on_stage(self, run_id: UUID, stage: Stage) -> None:
        """Ignore the stage event."""
        return None


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _wire_call(call: Any) -> dict[str, Any]:
    """Return one tool call in the OpenAI shape."""
    if isinstance(call, Mapping) and "function" in call:
        return dict(call)
    return ToolCall.from_wire(call).to_wire()


def build_model_messages(
    system_prompt: str | None, messages: Sequence[MessageDTO]
) -> list[dict[str, Any]]:
    """
    Build the model context with one tool group per assistant turn.

    Anthropic requires the tool rows of a turn to follow the assistant message
    in call order. A tool row that no assistant turn claims is left out, so the
    provider never sees an orphan.

    Args:
        system_prompt: The system prompt of the agent config, or None.
        messages: The stored messages, in chronological order.

    Returns:
        Provider neutral message dictionaries.
    """
    out: list[dict[str, Any]] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    rows_by_call: dict[str, MessageDTO] = {}
    for row in messages:
        if row.role == "tool" and row.tool_call_id:
            rows_by_call.setdefault(str(row.tool_call_id), row)

    for row in messages:
        if row.role == "tool":
            continue
        if row.role == "assistant" and row.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": row.content or "",
                    "tool_calls": [_wire_call(call) for call in row.tool_calls],
                }
            )
            for call_id in tool_call_ids(row):
                match = rows_by_call.get(call_id)
                if match is not None:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": match.tool_name or "",
                            "content": match.content or "",
                        }
                    )
            continue
        out.append({"role": row.role, "content": row.content or ""})
    return out


def estimate_tokens(messages: Sequence[MessageDTO]) -> int:
    """
    Estimate the context size in tokens.

    The estimate is cheap and stable: four characters per token, plus a small
    constant per message. It is a gate for the compaction stage, not a bill.

    Args:
        messages: The stored messages.

    Returns:
        The estimated token count.
    """
    total = 0
    for row in messages:
        total += 4 + len(row.content or "") // 4
        for call in row.tool_calls or []:
            total += len(json.dumps(call, default=str)) // 4
    return total


@dataclass(frozen=True)
class AssembledContext:
    """The result of stage one."""

    model_messages: list[dict[str, Any]]
    assistant: MessageDTO | None
    tool_rows: list[MessageDTO]
    tokens: int
    terminal_turn: bool = False
    """True when the last row is an assistant message with no tool call.

    The run hid a kill between the infer stage and the record stage, so the
    final answer is already stored and the loop must not call the model again.
    """

    def outstanding_ids(self) -> list[str]:
        """Return the ids of the last assistant turn that have no tool row."""
        if self.assistant is None or not self.assistant.tool_calls:
            return []
        answered = {str(row.tool_call_id) for row in self.tool_rows}
        return [cid for cid in tool_call_ids(self.assistant) if cid not in answered]


@dataclass
class DispatchOutcome:
    """The result of stage five."""

    executed: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    enqueued: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    awaiting: list[str] = field(default_factory=list)
    jobs: list[UUID] = field(default_factory=list)
    approvals: list[UUID] = field(default_factory=list)

    def merge(self, other: "DispatchOutcome") -> None:
        """Add the counts of another outcome to this one."""
        for name in (
            "executed",
            "reused",
            "enqueued",
            "resolved",
            "refused",
            "written",
            "awaiting",
            "jobs",
            "approvals",
        ):
            getattr(self, name).extend(getattr(other, name))


@dataclass(frozen=True)
class LoopIteration:
    """One pass of the pipeline. A test reads this to check the stage order."""

    run: RunDTO
    stages: tuple[str, ...]
    model_called: bool = False
    executed: tuple[str, ...] = ()
    written: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    awaiting: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class RunLoop:
    """Run the six stages over one run until it stops or waits."""

    def __init__(
        self,
        *,
        journal: RunJournal,
        model: LLMClientProtocol | None = None,
        tools: ToolExecutor | None = None,
        policy: PartitionPolicy | None = None,
        compactor: CompactionProvider | None = None,
        observer: StageObserver | None = None,
        tool_schemas: Sequence[Mapping[str, Any]] | None = None,
        cost: Any | None = None,
        max_iterations: int = 40,
        context_token_limit: int = 100_000,
        compact_ratio: float = 0.7,
    ) -> None:
        self.journal = journal
        self.model = model
        self.tools = tools
        self.policy = policy
        self.compactor = compactor
        self.observer = observer or NullObserver()
        self.cost = cost
        self.tool_schemas: list[dict[str, Any]] | None = (
            [dict(schema) for schema in tool_schemas] if tool_schemas else None
        )
        self.max_iterations = max_iterations
        self.context_token_limit = context_token_limit
        self.compact_ratio = compact_ratio
        # The chat session of the run under work. Every row that the loop
        # writes carries it, so the memory indexer sees the whole session.
        self._session_id: UUID | None = None

    # -- public API -------------------------------------------------------

    async def run(self, run_id: UUID) -> RunDTO:
        """
        Drive the run until it completes, fails, or waits for another actor.

        Args:
            run_id: The run to drive.

        Returns:
            The final run row of this call.

        Raises:
            LoopLimitExceeded: The run did not stop inside ``max_iterations``.
            InvariantViolation: The history breaks the barrier.
        """
        last: RunDTO | None = None
        for _ in range(self.max_iterations):
            iteration = await self.iterate(run_id)
            last = iteration.run
            if last.status in TERMINAL_RUN_STATUSES or last.status in AWAITING_RUN_STATUSES:
                return last
        if last is None:
            raise RunNotFound(f"run {run_id} is absent from the journal")
        raise LoopLimitExceeded(
            f"run {run_id} is still {last.status!r} after {self.max_iterations} iterations"
        )

    async def iterate(self, run_id: UUID) -> LoopIteration:
        """
        Run one pass of the pipeline.

        Args:
            run_id: The run to advance by one step.

        Returns:
            The iteration summary.
        """
        stages: list[str] = []

        run = await self.journal.get_run(run_id)
        if run is None:
            raise RunNotFound(f"run {run_id} is absent from the journal")
        if RunStateMachine.is_terminal(run.status):
            return LoopIteration(run=run, stages=())

        config = await self._config_for(run)
        run = await self._enter_running(run)

        messages = await self.journal.list_messages(run_id)
        self._session_id = next(
            (row.session_id for row in messages if row.session_id is not None), None
        )

        # Stage 1: assemble.
        assembled = self._assemble(config, messages)
        stages.append("assemble")
        await self.observer.on_stage(run_id, "assemble")

        # Stage 2: compact check.
        compacted_messages = await self._compact_check(run, assembled, messages)
        if list(compacted_messages) != list(messages):
            messages = compacted_messages
            assembled = self._assemble(config, messages)
        stages.append("compact_check")
        await self.observer.on_stage(run_id, "compact_check")

        outstanding = assembled.outstanding_ids()
        if assembled.terminal_turn:
            # A kill between stage three and stage six leaves the final answer as
            # the last row. Complete the run and do not spend a second call.
            run = await self._record_completion(
                run, content=(assembled.assistant.content if assembled.assistant else "")
            )
            stages.append("record")
            await self.observer.on_stage(run_id, "record")
            return LoopIteration(run=run, stages=tuple(stages))

        if outstanding:
            outcome = await self._dispatch_calls(run, assembled, outstanding)
            stages.append("dispatch")
            await self.observer.on_stage(run_id, "dispatch")
            run = await self._record_dispatch(run, config, outcome)
            stages.append("record")
            await self.observer.on_stage(run_id, "record")
            return self._iteration(run, stages, outcome)

        if self.model is None:
            raise LoopDependencyMissing("the loop has no model client")

        # Stage 3: infer. The barrier is the hard assertion before the call.
        # The stage ends only after the assistant message is a row, so a kill
        # after this stage never loses a paid model response.
        assert_run_barrier(messages)
        response = await self.model.complete(
            assembled.model_messages,
            tools=self.tool_schemas,
            temperature=0.0,
            tier="reasoning",
        )
        assistant = await self._record_assistant(run, response)
        stages.append("infer")
        await self.observer.on_stage(run_id, "infer")

        # Charge the call. The rate lives in the price table, so a model with no
        # row costs nothing and the run keeps its token counts.
        if self.cost is not None:
            await self.cost.charge(
                run.id,
                config.model,
                int(response.prompt_tokens),
                int(response.completion_tokens),
            )

        calls = [ToolCall.from_wire(call) for call in assistant.tool_calls or []]

        if not calls:
            run = await self._record_completion(run, response)
            stages.append("record")
            await self.observer.on_stage(run_id, "record")
            return LoopIteration(
                run=run,
                stages=tuple(stages),
                model_called=True,
            )

        # Stage 4: partition.
        policy = self.policy or PartitionPolicy.from_config(config)
        partitions: dict[str, ToolPartition] = {
            call.id: policy.partition_for(call.name) for call in calls
        }
        for call in calls:
            await self._record_step(
                run.id,
                "tool_call",
                {
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "partition": partitions[call.id],
                },
            )
        stages.append("partition")
        await self.observer.on_stage(run_id, "partition")

        # Stage 5: dispatch the fresh calls.
        messages = await self.journal.list_messages(run_id)
        assembled = self._assemble(config, messages)
        outcome = await self._dispatch_calls(
            run, assembled, [call.id for call in calls], partitions=partitions
        )
        stages.append("dispatch")
        await self.observer.on_stage(run_id, "dispatch")

        # Stage 6: record.
        run = await self._record_dispatch(run, config, outcome, response=response)
        stages.append("record")
        await self.observer.on_stage(run_id, "record")

        return self._iteration(run, stages, outcome, model_called=True)

    # -- stages -----------------------------------------------------------

    async def _config_for(self, run: RunDTO) -> AgentConfigDTO:
        """Return the agent config of a run. A missing config stops the run."""
        config = await self.journal.get_config(run.config_id)
        if config is None:
            raise RunConfigMissing(
                f"run {run.id} names agent config {run.config_id}, which has no row"
            )
        return config

    async def _enter_running(self, run: RunDTO) -> RunDTO:
        """Move the run to ``running`` through the state machine."""
        if run.status == "running":
            return run
        RunStateMachine.require(run.status, "running", run.id)
        return await self.journal.update_run(run.id, status="running")

    def _assemble(
        self, config: AgentConfigDTO, messages: Sequence[MessageDTO]
    ) -> AssembledContext:
        """Stage one: build the context and locate the last assistant turn."""
        assistant: MessageDTO | None = None
        for row in reversed(messages):
            if row.role == "assistant":
                assistant = row
                break
        tool_rows: list[MessageDTO] = []
        if assistant is not None:
            tool_rows = [
                row for row in messages if row.role == "tool" and row.tool_call_id
            ]
        return AssembledContext(
            model_messages=build_model_messages(config.system_prompt, messages),
            assistant=assistant,
            tool_rows=tool_rows,
            tokens=estimate_tokens(messages),
            terminal_turn=bool(messages)
            and messages[-1].role == "assistant"
            and not messages[-1].tool_calls,
        )

    async def _compact_check(
        self,
        run: RunDTO,
        assembled: AssembledContext,
        messages: Sequence[MessageDTO],
    ) -> list[MessageDTO]:
        """Stage two: record a compaction request when the context is too large."""
        limit = int(self.context_token_limit * self.compact_ratio)
        if assembled.tokens <= limit:
            return list(messages)
        if self.compactor is not None:
            class _HybridMessage(dict[str, Any]):
                """Mapping + attribute wrapper so both CompactionService and MessageDTO consumers work."""

                def __init__(self, dto: MessageDTO) -> None:
                    data: dict[str, Any] = {
                        "id": str(dto.id),
                        "run_id": dto.run_id,
                        "session_id": dto.session_id,
                        "role": dto.role,
                        "content": dto.content,
                        "tool_calls": dto.tool_calls,
                        "tool_call_id": dto.tool_call_id,
                        "tool_name": dto.tool_name,
                        "tool_arguments": dto.tool_arguments,
                        "tool_result_reference": dto.tool_result_reference,
                        "created_at": dto.created_at,
                    }
                    super().__init__({k: v for k, v in data.items() if v is not None or k in ("role", "content")})
                    self._dto = dto

                def __getattr__(self, item: str) -> Any:
                    return getattr(self._dto, item)

            originals_by_id = {str(m.id): m for m in messages}
            hybrid_messages = [_HybridMessage(m) for m in messages]
            result: Any = await self.compactor.compact(run.id, hybrid_messages)  # type: ignore[arg-type]
            if hasattr(result, "messages") and isinstance(result.messages, Sequence):
                from datetime import datetime, timezone
                from uuid import uuid4

                compacted_dtos: list[MessageDTO] = []
                now = datetime.now(timezone.utc)
                for item in result.messages:
                    if isinstance(item, MessageDTO):
                        compacted_dtos.append(item)
                        continue
                    raw_id = str(item.get("id") or "")
                    orig = originals_by_id.get(raw_id)
                    msg_id = orig.id if orig is not None else uuid4()
                    compacted_dtos.append(
                        MessageDTO(
                            id=msg_id,
                            run_id=run.id,
                            session_id=orig.session_id if orig is not None else self._session_id,
                            role=str(item.get("role", "assistant")),
                            content=item.get("content"),
                            tool_calls=item.get("tool_calls") if "tool_calls" in item else (orig.tool_calls if orig else None),
                            tool_call_id=item.get("tool_call_id") if "tool_call_id" in item else (orig.tool_call_id if orig else None),
                            tool_name=item.get("tool_name") if "tool_name" in item else (orig.tool_name if orig else None),
                            tool_arguments=item.get("tool_arguments") if "tool_arguments" in item else (orig.tool_arguments if orig else None),
                            tool_result_reference=item.get("tool_result_reference") if "tool_result_reference" in item else (orig.tool_result_reference if orig else None),
                            created_at=orig.created_at if orig is not None else now,
                        )
                    )
                replace_fn = getattr(self.journal, "replace_messages", None)
                if callable(replace_fn):
                    await replace_fn(run.id, compacted_dtos)
                return compacted_dtos
            if result:
                return await self.journal.list_messages(run.id)
        await self._record_step(
            run.id,
            "thought",
            {
                "stage": "compact_check",
                "estimated_tokens": assembled.tokens,
                "limit": limit,
                "compaction_required": self.compactor is None,
            },
        )
        return list(messages)

    async def _dispatch_calls(
        self,
        run: RunDTO,
        assembled: AssembledContext,
        call_ids: Sequence[str],
        partitions: Mapping[str, ToolPartition] | None = None,
    ) -> DispatchOutcome:
        """
        Stage five: satisfy the given tool call ids, or report what is missing.

        The stage is idempotent. A call that already has a tool row is reused and
        never runs again. A background call with a completed job gets its row. An
        approved external call runs. A rejected external call gets the structured
        rejection result. A pending approval or a pending job leaves the id
        outstanding.
        """
        outcome = DispatchOutcome()
        if assembled.assistant is None:
            return outcome
        policy = self.policy or PartitionPolicy.from_config(await self._config_for(run))

        rows = {str(row.tool_call_id): row for row in assembled.tool_rows}
        approvals = {row.tool_call_id: row for row in await self.journal.list_approvals(run.id)}
        jobs = {str(job.payload.get("tool_call_id")): job for job in await self.journal.list_jobs(run.id)}

        for call in self._calls_by_id(assembled.assistant):
            if call.id not in call_ids:
                continue
            if call.id in rows:
                outcome.reused.append(call.id)
                continue

            partition = (partitions or {}).get(call.id) or policy.partition_for(call.name)

            if partition == "local":
                await self._write_tool_row(run, call, await self._run_local(call))
                outcome.executed.append(call.id)
                outcome.written.append(call.id)
                continue

            if partition == "background":
                job = jobs.get(call.id)
                if job is None:
                    job = await self.journal.enqueue_job(
                        job_type=BACKGROUND_JOB_TYPE,
                        payload={
                            "run_id": str(run.id),
                            "kind": "tool_call",
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "arguments": call.arguments,
                        },
                    )
                    outcome.jobs.append(job.id)
                    outcome.enqueued.append(call.id)
                elif job.status == "completed":
                    result = job.payload.get("result")
                    await self._write_tool_row(
                        run, call, result if isinstance(result, Mapping) else {}
                    )
                    outcome.resolved.append(call.id)
                    outcome.written.append(call.id)
                    continue
                outcome.awaiting.append(call.id)
                continue

            approval = approvals.get(call.id)
            if approval is None:
                approval = await self.journal.request_approval(
                    run_id=run.id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
                outcome.approvals.append(approval.id)
            if approval.status == "pending":
                outcome.awaiting.append(call.id)
                continue
            if approval.status == "approved":
                await self._write_tool_row(
                    run, call, await self._run_external(call)
                )
                outcome.resolved.append(call.id)
                outcome.written.append(call.id)
                continue
            await self._write_tool_row(
                run,
                call,
                {
                    **USER_REJECTED_RESULT,
                    "decision_note": approval.decision_note or "",
                },
            )
            outcome.refused.append(call.id)
            outcome.written.append(call.id)

        return outcome

    async def _run_local(self, call: ToolCall) -> Mapping[str, Any]:
        """Run one local tool. A broken executor becomes a failed tool result."""
        if self.tools is None:
            raise LoopDependencyMissing("the loop has no tool executor")
        try:
            result = await self.tools.execute(call.name, call.arguments)
        except Exception as exc:  # noqa: BLE001 - the barrier must stay satisfied
            return {"error": type(exc).__name__, "message": str(exc)}
        return result if isinstance(result, Mapping) else {"content": result}

    async def _run_external(self, call: ToolCall) -> Mapping[str, Any]:
        """Run one approved external tool. A broken executor becomes a result."""
        if self.tools is None:
            raise LoopDependencyMissing("the loop has no tool executor")
        try:
            result = await self.tools.execute_external(call.name, call.arguments)
        except Exception as exc:  # noqa: BLE001 - the barrier must stay satisfied
            return {"error": type(exc).__name__, "message": str(exc)}
        return result if isinstance(result, Mapping) else {"content": result}

    async def _write_tool_row(
        self, run: RunDTO, call: ToolCall, result: Mapping[str, Any]
    ) -> MessageDTO:
        """Write the one tool row that answers a tool call id."""
        row = await self.journal.append_message(
            MessageCreateDTO(
                role="tool",
                run_id=run.id,
                session_id=self._session_id,
                content=json.dumps(dict(result), default=str, sort_keys=True),
                tool_call_id=call.id,
                tool_name=call.name,
                tool_arguments=call.arguments,
            )
        )
        await self._record_step(
            run.id,
            "tool_result",
            {"tool_call_id": call.id, "tool_name": call.name},
        )
        return row

    async def _record_assistant(
        self, run: RunDTO, response: LLMResponseDTO
    ) -> MessageDTO:
        """Persist the assistant message of one model call."""
        calls = [
            ToolCall(id=call.id, name=call.name, arguments=dict(call.arguments)).to_wire()
            for call in response.tool_calls
        ]
        assistant = await self.journal.append_message(
            MessageCreateDTO(
                role="assistant",
                run_id=run.id,
                session_id=self._session_id,
                content=response.content,
                tool_calls=calls or None,
            )
        )
        await self._record_step(
            run.id,
            "thought",
            {
                "content": response.content,
                "tool_call_ids": [call["id"] for call in calls],
                "total_tokens": response.total_tokens,
            },
        )
        return assistant

    async def _record_dispatch(
        self,
        run: RunDTO,
        config: AgentConfigDTO,
        outcome: DispatchOutcome,
        response: LLMResponseDTO | None = None,
    ) -> RunDTO:
        """Stage six: write the steps, the usage and the next run status."""
        for approval_id in outcome.approvals:
            await self._record_step(
                run.id, "approval_required", {"approval_id": str(approval_id)}
            )

        status = await self._next_status(run, outcome)
        token_usage = None
        if response is not None:
            token_usage = self._usage(run, response)
        RunStateMachine.require(run.status, status, run.id)
        run = await self.journal.update_run(
            run.id, status=status, token_usage=token_usage
        )
        return run

    async def _next_status(self, run: RunDTO, outcome: DispatchOutcome) -> str:
        """Return the status that the run must hold after a dispatch."""
        if not outcome.awaiting:
            return "running"
        approvals = await self.journal.list_approvals(run.id)
        pending = [
            row.tool_call_id for row in approvals if row.status == "pending"
        ]
        if any(call_id in pending for call_id in outcome.awaiting):
            return "awaiting_approval"
        return "awaiting_background_job"

    async def _record_completion(
        self,
        run: RunDTO,
        response: LLMResponseDTO | None = None,
        content: str | None = None,
    ) -> RunDTO:
        """Record a final answer and complete the run.

        Args:
            run: The current run row.
            response: The model response of this turn, or None after a restart.
            content: The final text, when it already sits in a message row.

        Returns:
            The completed run.
        """
        RunStateMachine.require(run.status, "completed", run.id)
        token_usage = self._usage(run, response) if response is not None else None
        run = await self.journal.update_run(
            run.id, status="completed", token_usage=token_usage
        )
        final = content if content is not None else ""
        if response is not None and response.content:
            final = final or response.content
        await self._record_step(run.id, "run_completed", {"content": final})
        return run

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _calls_by_id(assistant: MessageDTO) -> list[ToolCall]:
        """Return the tool calls of an assistant row in call order."""
        return [ToolCall.from_wire(call) for call in assistant.tool_calls or []]

    @staticmethod
    def _usage(run: RunDTO, response: LLMResponseDTO) -> dict[str, int]:
        """Add the usage of one model call to the stored usage."""
        previous = dict(run.token_usage or {})
        prompt = int(previous.get("prompt", 0)) + int(response.prompt_tokens)
        completion = int(previous.get("completion", 0)) + int(
            response.completion_tokens
        )
        # Some providers leave total_tokens at zero. The total is the sum of the
        # two counters, so derive it instead of trusting a missing field.
        return {"prompt": prompt, "completion": completion, "total": prompt + completion}

    async def _record_step(
        self, run_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Append one step event to the journal."""
        steps = await self.journal.list_steps(run_id)
        await self.journal.append_step(
            RunStepCreateDTO(
                run_id=run_id,
                step_number=len(steps) + 1,
                event_type=event_type,
                payload=payload,
            )
        )

    @staticmethod
    def _iteration(
        run: RunDTO,
        stages: Sequence[str],
        outcome: DispatchOutcome,
        model_called: bool = False,
    ) -> LoopIteration:
        """Build the iteration summary."""
        return LoopIteration(
            run=run,
            stages=tuple(stages),
            model_called=model_called,
            executed=tuple(outcome.executed),
            written=tuple(outcome.written),
            jobs=tuple(str(job_id) for job_id in outcome.jobs),
            approvals=tuple(str(approval_id) for approval_id in outcome.approvals),
            awaiting=tuple(outcome.awaiting),
        )
