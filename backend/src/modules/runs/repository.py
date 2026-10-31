"""Runs repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class AgentConfigDTO:
    """Configuration template defining agent prompts, tools, and constraints."""
    id: UUID
    name: str
    system_prompt: str
    model: str
    tools: list[str]
    skills: list[str]
    memory_scopes: list[str]
    approval_policy: dict[str, str]
    limits: dict[str, Any]


@dataclass(frozen=True)
class RunDTO:
    """Represents an agent execution run."""
    id: UUID
    config_id: UUID
    card_id: UUID | None
    parent_run_id: UUID | None
    depth: int
    status: str  # queued, running, awaiting_approval, awaiting_background_job, stale_thread_paused, completed, failed
    started_at: datetime
    ended_at: datetime | None
    token_usage: dict[str, int]
    cost_usd: float


@dataclass(frozen=True)
class RunCreateDTO:
    """Payload for scheduling a new agent run."""
    config_id: UUID
    card_id: UUID | None = None
    parent_run_id: UUID | None = None
    depth: int = 0
    status: str = "queued"


@dataclass(frozen=True)
class MessageDTO:
    """Single message entry in the conversation or run event stream."""
    id: UUID
    run_id: UUID | None
    session_id: UUID | None
    role: str  # user, assistant, system, tool
    content: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None
    tool_name: str | None
    tool_arguments: dict[str, Any] | None
    tool_result_reference: str | None
    created_at: datetime


@dataclass(frozen=True)
class MessageCreateDTO:
    """Payload for inserting a message turn."""
    role: str
    run_id: UUID | None = None
    session_id: UUID | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_result_reference: str | None = None


@dataclass(frozen=True)
class RunStepDTO:
    """Incremental execution event emitted by the agent loop."""
    id: UUID
    run_id: UUID
    step_number: int
    event_type: str  # thought, tool_call, tool_result, approval_required, run_completed, run_failed
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class RunStepCreateDTO:
    """Payload for recording a step event."""
    run_id: UUID
    step_number: int
    event_type: str
    payload: dict[str, Any]


@runtime_checkable
class RunsRepository(Protocol):
    """Protocol contract for agent run state, turns, steps, and configurations."""

    async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
        """
        Retrieve a run by its ID.

        Args:
            run_id: UUID of the run.

        Returns:
            RunDTO if found, None otherwise.
        """
        ...

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        """
        Create and persist a new run in initial status.

        Args:
            data: Run creation DTO.

        Returns:
            The created RunDTO.
        """
        ...

    async def update_run_status(
        self,
        run_id: UUID,
        status: str,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        """
        Update execution status, timestamps, and cost tracking of a run.

        Args:
            run_id: UUID of the run.
            status: New run status string.
            ended_at: Optional completion/failure timestamp.
            token_usage: Optional token consumption metrics.
            cost_usd: Optional estimated run cost in USD.

        Returns:
            Updated RunDTO.
        """
        ...

    async def save_message(self, data: MessageCreateDTO) -> MessageDTO:
        """
        Persist a message turn (user, assistant, tool result) to database.

        Args:
            data: Message turn creation DTO.

        Returns:
            The persisted MessageDTO.
        """
        ...

    async def get_messages_for_run(self, run_id: UUID) -> list[MessageDTO]:
        """
        Retrieve all messages belonging to a run in chronological order.

        Args:
            run_id: UUID of the run.

        Returns:
            List of MessageDTOs ordered by creation time ascending.
        """
        ...

    async def save_run_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        """
        Record a granular execution event step for live streaming and debugging.

        Args:
            data: Step event creation DTO.

        Returns:
            The persisted RunStepDTO.
        """
        ...

    async def get_steps_since(self, run_id: UUID, step_number: int) -> list[RunStepDTO]:
        """
        Fetch step events strictly greater than the given step number for SSE resume.

        Args:
            run_id: UUID of the run.
            step_number: High-water mark step number previously acknowledged.

        Returns:
            List of RunStepDTOs ordered by step_number ascending.
        """
        ...

    async def get_agent_config(self, config_id: UUID) -> AgentConfigDTO | None:
        """
        Retrieve an agent configuration by its UUID.

        Args:
            config_id: UUID of the configuration.

        Returns:
            AgentConfigDTO if found, None otherwise.
        """
        ...

    async def get_agent_config_by_name(self, name: str) -> AgentConfigDTO | None:
        """
        Retrieve an agent configuration by unique system name.

        Args:
            name: String name identifier (e.g. 'card-reply-drafter').

        Returns:
            AgentConfigDTO if found, None otherwise.
        """
        ...
