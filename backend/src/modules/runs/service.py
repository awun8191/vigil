"""Runs service: the durable run record and the stateless six-stage loop.

The service holds no run state. Every method reads and writes the journal, so a
killed worker resumes from the rows alone. The loop lives in :mod:`loop`, the
state machine and the journal in :mod:`state`, and the barrier invariant in
:mod:`barrier`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol, SandboxRunnerProtocol
from src.modules.runs.barrier import InvariantViolation
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
    ) -> None:
        self.session = session
        self.settings = settings
        self.sandbox = sandbox
        self.llm = llm
        self.tools = tools
        self.policy = policy
        self.journal: RunJournal = journal or self._default_journal(session)

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
            max_iterations=max_iterations,
        )

    # -- RunsRepository surface -------------------------------------------

    async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
        """Return one run by id."""
        return await self.journal.get_run(run_id)

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        """Insert one run row in its initial status."""
        return await self.journal.create_run(data)

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
    ) -> RunDTO:
        """
        Create a run, store the opening prompt, and queue its execution.

        The run starts in ``queued``. The execute job is a row, so the worker
        that claims it rebuilds everything from the database.

        Args:
            config_id: Agent configuration UUID.
            card_id: Optional card UUID.
            initial_prompt: Optional opening user message.

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
                MessageCreateDTO(run_id=run.id, role="user", content=initial_prompt)
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
        loop = self.build_loop(observer=observer)
        try:
            return await loop.run(run_id)
        except (InvariantViolation, LoopDependencyMissing) as exc:
            await self._fail_run(run_id, exc)
            raise

    async def resume_run(
        self,
        run_id: UUID,
        *,
        observer: StageObserver | None = None,
    ) -> RunDTO:
        """Resume a run that waited for an approval or a background job."""
        return await self.process(run_id, observer=observer)

    async def cancel_run(self, run_id: UUID) -> RunDTO:
        """
        Cancel a run.

        Args:
            run_id: Run UUID.

        Returns:
            The run in ``failed``.

        Raises:
            NotFoundError: The run has no row.
            InvalidRunTransition: The run already stopped.
        """
        updated = await self.update_run_status(run_id, status="failed")
        await self.journal.append_step(
            RunStepCreateDTO(
                run_id=run_id,
                step_number=len(await self.journal.list_steps(run_id)) + 1,
                event_type="run_failed",
                payload={"reason": "cancelled"},
            )
        )
        return updated

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
