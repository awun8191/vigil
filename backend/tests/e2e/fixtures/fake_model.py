"""A scripted model client for the Phase 4 end-to-end test.

The client returns two responses: the first proposes three tool calls, the
second is the final answer. It records every request, so the test can prove
that the loop called the model twice and that the tool results reached the
second call in call order.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from src.integrations.repositories import LLMResponseDTO, LLMToolCallDTO

REPORT_TEXT = "# Phase 4 report\n\nDelivered from the sandbox.\n"
"""Exact bytes that the sandbox writes and the delivery tool copies."""

REPORT_NAME = "phase4-report.md"
"""Host name of the delivered artifact."""

EXTERNAL_TOOL = "gmail_send"
"""Connector tool that needs an approval."""

EXTERNAL_ARGUMENTS: dict[str, Any] = {
    "to": "ops@example.com",
    "subject": "Phase 4 report",
    "body": REPORT_TEXT,
}
"""Arguments of the external call."""

SCRIPT_TEMPLATE = Path(__file__).with_name("write_report.sh")
"""Shell command template. The model fills it and runs it through ``sh -c``."""

BASH_CALL_ID = "call_bash_1"
EXTERNAL_CALL_ID = "call_mail_1"
DELIVER_CALL_ID = "call_deliver_1"


def write_report_command() -> str:
    """Return the sandbox command that writes the report file."""
    template = SCRIPT_TEMPLATE.read_text(encoding="utf-8")
    script = template.replace("{report}", shlex.quote(REPORT_TEXT))
    return "sh -c " + shlex.quote(script)


def first_response() -> LLMResponseDTO:
    """Return the response that proposes one shell call, one mail call and one delivery."""
    return LLMResponseDTO(
        content="I will build the report first.",
        tool_calls=[
            LLMToolCallDTO(
                id=BASH_CALL_ID,
                name="bash",
                arguments={"command": write_report_command(), "timeout_seconds": 60},
            ),
            LLMToolCallDTO(
                id=EXTERNAL_CALL_ID,
                name=EXTERNAL_TOOL,
                arguments=dict(EXTERNAL_ARGUMENTS),
            ),
            LLMToolCallDTO(
                id=DELIVER_CALL_ID,
                name="deliver_file",
                arguments={"path": "/workspace/report.md", "name": REPORT_NAME},
            ),
        ],
        prompt_tokens=120,
        completion_tokens=40,
        total_tokens=160,
        finish_reason="tool_calls",
        model="fake-model",
    )


def final_response() -> LLMResponseDTO:
    """Return the final answer of the run."""
    return LLMResponseDTO(
        content="The report is delivered and the email is approved.",
        tool_calls=[],
        prompt_tokens=200,
        completion_tokens=20,
        total_tokens=220,
        finish_reason="stop",
        model="fake-model",
    )


def background_response() -> LLMResponseDTO:
    """Return a response that proposes one background tool call."""
    return LLMResponseDTO(
        content="I will build the report in the background.",
        tool_calls=[
            LLMToolCallDTO(
                id=BASH_CALL_ID,
                name="bash",
                arguments={"command": write_report_command(), "timeout_seconds": 60},
            )
        ],
        prompt_tokens=80,
        completion_tokens=20,
        total_tokens=100,
        finish_reason="tool_calls",
        model="fake-model",
    )


class FakeModel:
    """A model client with a fixed script and a call log."""

    def __init__(self, responses: list[LLMResponseDTO] | None = None) -> None:
        self.responses: list[LLMResponseDTO] = (
            list(responses)
            if responses is not None
            else [first_response(), final_response()]
        )
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

    def tool_result_order(self, call_index: int) -> list[str]:
        """Return the tool call ids of the tool messages in one model request."""
        messages = self.calls[call_index]["messages"]
        return [
            str(message["tool_call_id"])
            for message in messages
            if message.get("role") == "tool"
        ]
