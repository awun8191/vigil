"""Runs service: the durable run record and the stateless six-stage loop.

The service holds no run state. Every method reads and writes the journal, so a
killed worker resumes from the rows alone. The loop lives in :mod:`loop`, the
state machine and the journal in :mod:`state`, and the barrier invariant in
:mod:`barrier`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Sequence
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.logging import get_logger
from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol, SandboxRunnerProtocol
from src.modules.jobs.repository import JobDTO, JobsRepository
from src.modules.runs.barrier import InvariantViolation
from src.modules.runs.tools import openai_tool_declarations
from src.modules.runs.loop import (
    LoopDependencyMissing,
    RunLoop,
    StageObserver,
    ToolExecutor,
)
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    MessageDTO,
    RunCreateDTO,
    RunDTO,
    RunStepCreateDTO,
    RunStepDTO,
)
from src.modules.runs.state import (
    InMemoryRunJournal,
    InvalidRunTransition,
    MaxDepthExceeded,
    PartitionPolicy,
    RunJournal,
    RunStateMachine,
    SqlRunJournal,
)


class RunsService:
    """Agent execution harness that satisfies RunsRepository through duck typing.

    Args:
        session: Database session, or None in a test.
        settings: Application settings.
        sandbox: Optional sandbox runner. The loop never calls it directly.
        llm: Optional model client. The loop needs it to run the infer stage.
        journal: Optional journal override. It defaults to the SQL journal when a
            session is present, and to the in-memory journal otherwise.
        tools: Optional tool executor. The loop needs it to run a tool call.
        policy: Optional partition policy. It defaults to the agent config.
    """

    def __init__(
        self,
        session: AsyncSession | None,
        settings: Settings,
        sandbox: SandboxRunnerProtocol | None = None,
        llm: LLMClientProtocol | None = None,
        journal: RunJournal | None = None,
        tools: ToolExecutor | None = None,
        policy: PartitionPolicy | None = None,
        jobs: JobsRepository | None = None,
        pricing: Any | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.sandbox = sandbox
        self.llm = llm
        self.tools = tools
        self.policy = policy
        self.pricing = pricing
        self.journal: RunJournal = journal or self._default_journal(session)
        self.jobs = jobs

    @staticmethod
    def _default_journal(session: AsyncSession | None) -> RunJournal:
        """Return the SQL journal for a session, and the test double without one."""
        if session is None:
            return InMemoryRunJournal()
        return SqlRunJournal(session)

    def build_loop(
        self,
        *,
        observer: StageObserver | None = None,
        max_iterations: int = 40,
        tool_schemas: Sequence[dict[str, Any]] | None = None,
    ) -> RunLoop:
        """
        Build a loop over the current dependencies.

        Args:
            observer: Optional stage observer.
            max_iterations: Iteration cap.

        Returns:
            The loop.
        """
        return RunLoop(
            journal=self.journal,
            model=self.llm,
            tools=self.tools,
            policy=self.policy,
            observer=observer,
            tool_schemas=tool_schemas,
            cost=self,
            max_iterations=max_iterations,
        )

    # -- RunsRepository surface -------------------------------------------

    async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
        """Return one run by id."""
        return await self.journal.get_run(run_id)

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        """Insert one run row in its initial status."""
        if data.parent_run_id is not None:
            data = await self._child_create_data(data)
            child = await self.journal.create_run(data)
            assert data.parent_run_id is not None
            await self._isolate_child_workspace(data.parent_run_id, child.id)
            return child
        return await self.journal.create_run(data)

    async def _child_create_data(self, data: RunCreateDTO) -> RunCreateDTO:
        """Return a child create DTO with the depth the parent dictates.

        The child depth is always one above the parent, so a caller can
        never hand-craft a depth jump and a cycle is impossible: the child
        id is minted fresh after the parent row already exists.
        """
        parent = await self.journal.get_run(data.parent_run_id)  # type: ignore[arg-type]
        if parent is None:
            raise NotFoundError(f"Parent run {data.parent_run_id} not found")
        depth = parent.depth + 1
        if depth > self.settings.MAX_RUN_DEPTH:
            raise MaxDepthExceeded(depth, self.settings.MAX_RUN_DEPTH)
        return RunCreateDTO(
            config_id=data.config_id,
            card_id=data.card_id,
            parent_run_id=parent.id,
            depth=depth,
            status=data.status,
        )

    async def create_child_run(
        self,
        parent_run_id: UUID,
        config_id: UUID | None = None,
        card_id: UUID | None = None,
        initial_prompt: str | None = None,
    ) -> RunDTO:
        """Create a sub-agent run under an existing parent run.

        The child nests one level deeper than the parent, owns a fresh
        workspace ref that it never shares with the parent, and queues an
        execute job like a top-level run.

        Args:
            parent_run_id: Run that spawns the child. It must exist.
            config_id: Agent config of the child. It defaults to the parent.
            card_id: Optional card of the child. It defaults to the parent.
            initial_prompt: Optional opening user message of the child.

        Returns:
            The queued child run.

        Raises:
            NotFoundError: The parent run has no row.
            MaxDepthExceeded: The child would nest past MAX_RUN_DEPTH.
        """
        parent = await self.journal.get_run(parent_run_id)
        if parent is None:
            raise NotFoundError(f"Parent run {parent_run_id} not found")
        child = await self.create_run(
            RunCreateDTO(
                config_id=config_id or parent.config_id,
                card_id=card_id if card_id is not None else parent.card_id,
                parent_run_id=parent.id,
                status="queued",
            )
        )
        if initial_prompt:
            await self.journal.append_message(
                MessageCreateDTO(
                    run_id=child.id,
                    role="user",
                    content=initial_prompt,
                )
            )
        await self.journal.enqueue_job(
            job_type="execute_run",
            payload={"run_id": str(child.id), "kind": "run"},
        )
        return child

    async def _isolate_child_workspace(self, parent_id: UUID, child_id: UUID) -> str:
        """Give the child a workspace ref that differs from the parent."""
        parent_ref = await self.journal.get_workspace_ref(parent_id)
        if parent_ref is None:
            parent_ref = f"ws_{uuid4().hex}"
            await self.journal.set_workspace_ref(parent_id, parent_ref)
        child_ref = f"ws_{uuid4().hex}"
        while child_ref == parent_ref:
            child_ref = f"ws_{uuid4().hex}"
        await self.journal.set_workspace_ref(child_id, child_ref)
        return child_ref

    async def charge(
        self,
        run_id: UUID,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> int:
        """Charge one model call to its run and return the micros added.

        The rate comes from the price table. A model with no row costs zero, so
        the run still records its token counts.

        Args:
            run_id: Run that pays for the call.
            model: Model name of the call.
            prompt_tokens: Prompt tokens of the call.
            completion_tokens: Completion tokens of the call.

        Returns:
            Whole micros charged.
        """
        if self.pricing is None:
            return 0
        micros = await self.pricing.cost_micros(
            model, prompt_tokens, completion_tokens
        )
        if micros:
            await self.record_cost_micros(run_id, micros)
        return micros

    async def record_cost_micros(self, run_id: UUID, amount_micros: int) -> RunDTO:
        """Charge one run and fail any ancestor whose budget is exceeded.

        A child charges its parent: the cost counts toward every ancestor
        budget up to the root. When the subtree sum of an ancestor exceeds
        its ``limits.max_cost_micros``, that ancestor fails.

        Args:
            run_id: Run that spent the amount.
            amount_micros: Non-negative spend in micro-USD.

        Returns:
            The charged run.

        Raises:
            NotFoundError: The run has no row.
        """
        run = await self.journal.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        await self.journal.add_cost_micros(run_id, amount_micros)
        for ancestor_id in await self._ancestor_ids(run_id):
            limit = await self._budget_limit_micros(ancestor_id)
            if limit <= 0:
                continue
            total = await self._subtree_cost_micros(ancestor_id)
            if total > limit:
                await self._fail_run(
                    ancestor_id,
                    RuntimeError(
                        f"run budget exceeded: subtree cost {total} micros "
                        f"is above max_cost_micros {limit}"
                    ),
                )
        updated = await self.journal.get_run(run_id)
        assert updated is not None
        return updated

    async def _ancestor_ids(self, run_id: UUID) -> list[UUID]:
        """Return the ancestor chain from the parent up to the root."""
        ancestors: list[UUID] = []
        seen: set[UUID] = {run_id}
        current = await self.journal.get_run(run_id)
        while current is not None and current.parent_run_id is not None:
            parent_id = current.parent_run_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            ancestors.append(parent_id)
            current = await self.journal.get_run(parent_id)
        return ancestors

    async def _budget_limit_micros(self, run_id: UUID) -> int:
        """Return the budget of one run in micro-USD, or 0 with no limit."""
        run = await self.journal.get_run(run_id)
        if run is None:
            return 0
        config = await self.journal.get_config(run.config_id)
        if config is None:
            return 0
        try:
            return int(config.limits.get(self.settings.RUN_BUDGET_LIMIT_KEY, 0))
        except (TypeError, ValueError):
            return 0

    async def _subtree_cost_micros(self, run_id: UUID) -> int:
        """Return the cost of one run plus every descendant in micro-USD."""
        total = await self.journal.get_cost_micros(run_id)
        for child in await self.journal.list_child_runs(run_id):
            total += await self._subtree_cost_micros(child.id)
        return total

    async def update_run_status(
        self,
        run_id: UUID,
        status: str,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        """
        Change the status of a run through the state machine.

        Args:
            run_id: Run UUID.
            status: Target status.
            ended_at: Optional end timestamp.
            token_usage: Optional token counters.
            cost_usd: Optional run cost.

        Returns:
            The updated run.

        Raises:
            NotFoundError: The run has no row.
            InvalidRunTransition: The edge does not exist.
        """
        run = await self.journal.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        if status != run.status:
            RunStateMachine.require(run.status, status, run_id)
        return await self.journal.update_run(
            run_id,
            status=status,
            ended_at=ended_at,
            token_usage=token_usage,
            cost_usd=cost_usd,
        )

    async def save_message(self, data: MessageCreateDTO) -> MessageDTO:
        """Insert one message row."""
        return await self.journal.append_message(data)

    async def get_messages_for_run(self, run_id: UUID) -> list[MessageDTO]:
        """Return the messages of a run in chronological order."""
        return await self.journal.list_messages(run_id)

    async def save_run_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        """Record one step event."""
        return await self.journal.append_step(data)

    async def get_steps_since(self, run_id: UUID, step_number: int) -> list[RunStepDTO]:
        """Return the step events above a step number."""
        steps = await self.journal.list_steps(run_id)
        return [step for step in steps if step.step_number > step_number]

    async def get_agent_config(self, config_id: UUID) -> AgentConfigDTO | None:
        """Return one agent config by id."""
        return await self.journal.get_config(config_id)

    async def get_agent_config_by_name(self, name: str) -> AgentConfigDTO | None:
        """Return one agent config by unique name."""
        return await self.journal.get_config_by_name(name)

    async def save_agent_config(self, config: AgentConfigDTO) -> AgentConfigDTO:
        """Insert or replace one agent config."""
        return await self.journal.save_config(config)

    # -- domain orchestration ---------------------------------------------

    async def start_run(
        self,
        config_id: UUID,
        card_id: UUID | None = None,
        initial_prompt: str | None = None,
        session_id: UUID | None = None,
    ) -> RunDTO:
        """
        Create a run, store the opening prompt, and queue its execution.

        The run starts in ``queued``. The execute job is a row, so the worker
        that claims it rebuilds everything from the database.

        Args:
            config_id: Agent configuration UUID.
            card_id: Optional card UUID.
            initial_prompt: Optional opening user message.
            session_id: Optional chat session that owns the run. The session
                receives an index job when the run completes.

        Returns:
            The queued run.

        Raises:
            NotFoundError: The agent config has no row.
        """
        config = await self.journal.get_config(config_id)
        if config is None:
            raise NotFoundError(f"Agent config {config_id} not found")
        run = await self.journal.create_run(
            RunCreateDTO(config_id=config_id, card_id=card_id, status="queued")
        )
        if initial_prompt:
            await self.journal.append_message(
                MessageCreateDTO(
                    run_id=run.id,
                    session_id=session_id,
                    role="user",
                    content=initial_prompt,
                )
            )
        await self.journal.enqueue_job(
            job_type="execute_run",
            payload={"run_id": str(run.id), "kind": "run"},
        )
        return run

    async def process(
        self,
        run_id: UUID,
        *,
        observer: StageObserver | None = None,
    ) -> RunDTO:
        """
        Drive one run through the loop until it stops.

        The method builds the loop from the injected dependencies on each call,
        so a worker that claims a resume job holds no earlier state.

        Args:
            run_id: Run UUID.
            observer: Optional stage observer for tests.

        Returns:
            The run after the loop stops.

        Raises:
            NotFoundError: The run has no row.
            InvariantViolation: The history breaks the barrier. The run is
                marked failed and the error is raised again with no retry.
            LoopDependencyMissing: The service has no model client or no tools.
        """
        run = await self.journal.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        # Declare the tools this run may use. Without this the provider sees an
        # empty tool list and the model refuses work it could do.
        config = await self.journal.get_config(run.config_id)
        declared = list(config.tools) if config is not None else []
        loop = self.build_loop(
            observer=observer,
            tool_schemas=openai_tool_declarations(declared),
        )
        try:
            result = await loop.run(run_id)
        except (InvariantViolation, LoopDependencyMissing) as exc:
            await self._fail_run(run_id, exc)
            raise
        if result.status == "completed":
            await self.enqueue_index_for_completed_run(result)
        return result

    async def enqueue_index_for_completed_run(self, run: RunDTO) -> list[JobDTO]:
        """Enqueue one memory index job for each session of a completed run.

        This is the run completion producer. It writes queue rows and embeds
        nothing, so the request path never loads the model.

        Args:
            run: The run that just reached ``completed``.

        Returns:
            The jobs that this call enqueued.
        """
        if self.jobs is None and self.session is None:
            return []
        session_ids: list[UUID] = []
        for message in await self.journal.list_messages(run.id):
            if message.session_id is not None and message.session_id not in session_ids:
                session_ids.append(message.session_id)
        if not session_ids:
            return []

        from src.modules.memory.service import MemoryService

        memory = MemoryService(session=self.session, settings=self.settings)
        enqueued: list[JobDTO] = []
        for session_id in session_ids:
            try:
                enqueued.append(
                    await memory.enqueue_index_job(session_id, jobs=self.jobs)
                )
            except Exception as exc:  # the run is done, so a queue error is not a run error
                get_logger("virgil.runs.service").error(
                    "Cannot enqueue the index job of a completed run",
                    run_id=str(run.id),
                    session_id=str(session_id),
                    error=str(exc),
                )
        return enqueued

    async def resume_run(
        self,
        run_id: UUID,
        *,
        observer: StageObserver | None = None,
    ) -> RunDTO:
        """Resume a run that waited for an approval or a background job."""
        return await self.process(run_id, observer=observer)

    async def cancel_run(self, run_id: UUID) -> RunDTO:
        """Cancel a run and every live descendant under it.

        A child cannot outlive a cancelled parent, so the cancellation
        walks the tree and fails each non-terminal descendant.

        Args:
            run_id: Run UUID.

        Returns:
            The run in ``failed``.

        Raises:
            NotFoundError: The run has no row.
            InvalidRunTransition: The run already stopped.
        """
        updated = await self._cancel_one(run_id)
        for descendant_id in await self._descendant_ids(run_id):
            await self._cancel_one(descendant_id, strict=False)
        return updated

    async def _cancel_one(self, run_id: UUID, strict: bool = True) -> RunDTO:
        """Fail one run and record its cancellation step."""
        try:
            updated = await self.update_run_status(run_id, status="failed")
        except (NotFoundError, InvalidRunTransition):
            if strict:
                raise
            current = await self.journal.get_run(run_id)
            assert current is not None
            return current
        await self.journal.append_step(
            RunStepCreateDTO(
                run_id=run_id,
                step_number=len(await self.journal.list_steps(run_id)) + 1,
                event_type="run_failed",
                payload={"reason": "cancelled"},
            )
        )
        return updated

    async def _descendant_ids(self, run_id: UUID) -> list[UUID]:
        """Return every descendant id under one run, parents before children."""
        ordered: list[UUID] = []
        queue = [child.id for child in await self.journal.list_child_runs(run_id)]
        seen = {run_id}
        while queue:
            current_id = queue.pop(0)
            if current_id in seen:
                continue
            seen.add(current_id)
            ordered.append(current_id)
            queue.extend(
                child.id
                for child in await self.journal.list_child_runs(current_id)
            )
        return ordered

    async def stream_steps(
        self,
        run_id: UUID,
        since_step: int = 0,
    ) -> AsyncIterator[RunStepDTO]:
        """Stream the recorded step events of a run."""
        for step in await self.get_steps_since(run_id, since_step):
            yield step

    # -- helpers ----------------------------------------------------------

    async def _fail_run(self, run_id: UUID, error: Exception) -> None:
        """Mark a run failed and record one failure step."""
        run = await self.journal.get_run(run_id)
        if run is None or RunStateMachine.is_terminal(run.status):
            return
        try:
            RunStateMachine.require(run.status, "failed", run_id)
        except InvalidRunTransition:
            return
        await self.journal.update_run(run_id, status="failed")
        await self.journal.append_step(
            RunStepCreateDTO(
                run_id=run_id,
                step_number=len(await self.journal.list_steps(run_id)) + 1,
                event_type="run_failed",
                payload={
                    "error": type(error).__name__,
                    "message": str(error),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
