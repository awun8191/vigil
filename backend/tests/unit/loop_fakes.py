"""Test doubles for the run loop: a scripted model, a fake executor, an observer.

The loop takes a model client and a tool executor as protocols. These fakes
implement the small part of each contract that the loop calls, and they record
every call, so a test can prove that the loop did not run a tool twice.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from src.integrations.repositories import (
    LLMResponseDTO,
    LLMStreamChunkDTO,
    LLMToolCallDTO,
)
from src.modules.runs.repository import AgentConfigDTO, MessageDTO

__all__ = [
    "FakeToolExecutor",
    "LoopKilled",
    "RecordingObserver",
    "ScriptedModel",
    "agent_config",
    "tool_messages",
    "tool_response",
    "text_response",
]


class ScriptedModel:
    """A model client that returns scripted responses and records each request."""

    def __init__(self, responses: Sequence[LLMResponseDTO]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        """Record the request and return the next scripted response."""
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "temperature": temperature,
                "tier": tier,
            }
        )
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError(
                f"the model received call {index + 1} but the script has "
                f"{len(self.responses)} responses"
            )
        return self.responses[index]

    async def is_healthy(self) -> bool:
        """Report a healthy client."""
        return True

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ):
        """Yield one chunk per scripted response. The loop does not stream."""
        response = await self.complete(messages, tools, temperature, tier, max_tokens)
        yield LLMStreamChunkDTO(
            delta_content=response.content,
            tool_call_chunks=None,
            done=True,
            finish_reason=response.finish_reason,
        )

    async def embed_texts(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """Return zero vectors. The loop never embeds."""
        return [[0.0] * 384 for _ in texts]


class FakeToolExecutor:
    """A tool executor that records each call and never touches a sandbox."""

    def __init__(self) -> None:
        self.local: list[tuple[str, dict[str, Any]]] = []
        self.external: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Record a local call and return a successful result."""
        self.local.append((name, dict(arguments)))
        return {"ok": True, "content": f"{name} ran", "artifact_ref": None, "error": None}

    async def execute_external(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Record an external call and return a successful result."""
        self.external.append((name, dict(arguments)))
        return {"ok": True, "content": f"{name} sent", "artifact_ref": None, "error": None}

    def local_names(self) -> list[str]:
        """Return the local tool names in call order."""
        return [name for name, _ in self.local]

    def external_names(self) -> list[str]:
        """Return the external tool names in call order."""
        return [name for name, _ in self.external]


class LoopKilled(RuntimeError):
    """Raised by an observer to simulate a process that dies after a stage."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"the loop died after stage {stage!r}")


class RecordingObserver:
    """Record every completed stage. Optionally die after one of them."""

    def __init__(self, kill_after: str | None = None) -> None:
        self.stages: list[str] = []
        self.kill_after = kill_after

    async def on_stage(self, run_id: UUID, stage: str) -> None:
        """Record the stage, then raise when the test asked for a kill."""
        self.stages.append(stage)
        if self.kill_after is not None and stage == self.kill_after:
            raise LoopKilled(stage)


def tool_response(*calls: tuple[str, str, dict[str, Any]]) -> LLMResponseDTO:
    """
    Build a model response with parallel tool calls.

    Args:
        *calls: One ``(call_id, tool_name, arguments)`` tuple per call.

    Returns:
        The scripted response.
    """
    return LLMResponseDTO(
        content=None,
        tool_calls=[
            LLMToolCallDTO(id=call_id, name=name, arguments=arguments)
            for call_id, name, arguments in calls
        ],
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        finish_reason="tool_calls",
        model="scripted-model",
    )


def text_response(content: str) -> LLMResponseDTO:
    """Build a final response with no tool call."""
    return LLMResponseDTO(
        content=content,
        tool_calls=[],
        prompt_tokens=12,
        completion_tokens=6,
        total_tokens=18,
        finish_reason="stop",
        model="scripted-model",
    )


def agent_config(
    *,
    name: str = "loop-agent",
    model: str = "scripted-model",
    tools: Sequence[str] = ("read_file",),
    approval_policy: Mapping[str, str] | None = None,
    limits: Mapping[str, Any] | None = None,
    config_id: UUID | None = None,
) -> AgentConfigDTO:
    """Build an agent config for a loop test."""
    return AgentConfigDTO(
        id=config_id or uuid4(),
        name=name,
        system_prompt="You are a careful test agent.",
        model=model,
        tools=list(tools),
        skills=[],
        memory_scopes=["global"],
        approval_policy=dict(
            approval_policy
            or {"compile_artifact": "background", "gmail_send": "external"}
        ),
        limits=dict(limits or {"max_cost_micros": 1_000_000}),
    )


def tool_messages(call: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the tool messages of one recorded model request, in order."""
    return [
        message
        for message in call["messages"]
        if message.get("role") == "tool"
    ]


def tool_rows(messages: Sequence[MessageDTO]) -> list[MessageDTO]:
    """Return the tool rows of a run history, in order."""
    return [row for row in messages if row.role == "tool"]
