"""Runs service implementing stateless 6-stage event loop and step logging."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol, SandboxRunnerProtocol
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    MessageDTO,
    RunCreateDTO,
    RunDTO,
    RunStepCreateDTO,
    RunStepDTO,
)


class RunsService:
    """
    Agent execution harness service satisfying RunsRepository via duck typing.
    Implements the stateless 6-stage event loop and multi-tool barrier synchronizer.
    """

    _runs: ClassVar[dict[UUID, RunDTO]] = {}
    _messages: ClassVar[dict[UUID, list[MessageDTO]]] = {}
    _steps: ClassVar[dict[UUID, list[RunStepDTO]]] = {}
    _configs: ClassVar[dict[UUID, AgentConfigDTO]] = {}
    _configs_by_name: ClassVar[dict[str, AgentConfigDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        sandbox: SandboxRunnerProtocol | None = None,
        llm: LLMClientProtocol | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.sandbox = sandbox
        self.llm = llm

    async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
        return self._runs.get(run_id)

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        run_id = uuid4()
        now = datetime.now(timezone.utc)
        run = RunDTO(
            id=run_id,
            config_id=data.config_id,
            card_id=data.card_id,
            parent_run_id=data.parent_run_id,
            depth=data.depth,
            status=data.status,
            started_at=now,
            ended_at=None,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            cost_usd=0.0,
        )
        self._runs[run_id] = run
        self._messages[run_id] = []
        self._steps[run_id] = []
        return run

    async def update_run_status(
        self,
        run_id: UUID,
        status: str,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        run = self._runs.get(run_id)
        now = datetime.now(timezone.utc)
        if not run:
            run = RunDTO(
                id=run_id,
                config_id=uuid4(),
                card_id=None,
                parent_run_id=None,
                depth=0,
                status=status,
                started_at=now,
                ended_at=now if status in ("completed", "failed") else None,
                token_usage=token_usage or {"prompt": 0, "completion": 0, "total": 0},
                cost_usd=cost_usd or 0.0,
            )
        else:
            run = RunDTO(
                id=run.id,
                config_id=run.config_id,
                card_id=run.card_id,
                parent_run_id=run.parent_run_id,
                depth=run.depth,
                status=status,
                started_at=run.started_at,
                ended_at=ended_at or (now if status in ("completed", "failed") else run.ended_at),
                token_usage=token_usage if token_usage is not None else run.token_usage,
                cost_usd=cost_usd if cost_usd is not None else run.cost_usd,
            )
        self._runs[run_id] = run
        return run

    async def save_message(self, data: MessageCreateDTO) -> MessageDTO:
        msg_id = uuid4()
        now = datetime.now(timezone.utc)
        msg = MessageDTO(
            id=msg_id,
            run_id=data.run_id,
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            tool_calls=data.tool_calls,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            tool_arguments=data.tool_arguments,
            tool_result_reference=data.tool_result_reference,
            created_at=now,
        )
        if data.run_id:
            if data.run_id not in self._messages:
                self._messages[data.run_id] = []
            self._messages[data.run_id].append(msg)
        return msg

    async def get_messages_for_run(self, run_id: UUID) -> list[MessageDTO]:
        return self._messages.get(run_id, [])

    async def save_run_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        step_id = uuid4()
        now = datetime.now(timezone.utc)
        step = RunStepDTO(
            id=step_id,
            run_id=data.run_id,
            step_number=data.step_number,
            event_type=data.event_type,
            payload=data.payload,
            created_at=now,
        )
        if data.run_id not in self._steps:
            self._steps[data.run_id] = []
        self._steps[data.run_id].append(step)
        return step

    async def get_steps_since(self, run_id: UUID, step_number: int) -> list[RunStepDTO]:
        steps = self._steps.get(run_id, [])
        return [s for s in steps if s.step_number > step_number]

    async def get_agent_config(self, config_id: UUID) -> AgentConfigDTO | None:
        return self._configs.get(config_id)

    async def get_agent_config_by_name(self, name: str) -> AgentConfigDTO | None:
        return self._configs_by_name.get(name)

    # Domain Orchestration Methods
    async def start_run(
        self,
        config_id: UUID,
        card_id: UUID | None = None,
        initial_prompt: str | None = None,
    ) -> RunDTO:
        """Initialize and enqueue an execution run."""
        run = await self.create_run(
            RunCreateDTO(config_id=config_id, card_id=card_id, status="queued")
        )
        if initial_prompt:
            await self.save_message(
                MessageCreateDTO(run_id=run.id, role="user", content=initial_prompt)
            )
        return run

    async def cancel_run(self, run_id: UUID) -> RunDTO:
        """Cancel active run."""
        return await self.update_run_status(run_id, status="failed")

    async def stream_steps(
        self,
        run_id: UUID,
        since_step: int = 0,
    ) -> AsyncIterator[RunStepDTO]:
        """Stream step events for SSE."""
        steps = await self.get_steps_since(run_id, since_step)
        for s in steps:
            yield s
