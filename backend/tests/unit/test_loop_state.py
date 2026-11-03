"""Unit tests for the run state machine, the partition policy and the loop.

The loop is driven with a scripted model and a fake executor. No network, no
container and no provider key are involved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from src.config.settings import Settings
from src.modules.runs.loop import (
    BACKGROUND_JOB_TYPE,
    STAGES,
    RunLoop,
    estimate_tokens,
)
from src.modules.runs.repository import MessageCreateDTO, RunCreateDTO, RunDTO
from src.modules.runs.service import RunsService
from src.config.exceptions import ValidationError
from src.modules.runs.state import (
    RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    InMemoryRunJournal,
    InvalidRunTransition,
    PartitionPolicy,
    RunStateMachine,
    ToolCall,
)
from tests.unit.loop_fakes import (
    FakeToolExecutor,
    RecordingObserver,
    ScriptedModel,
    agent_config,
    text_response,
    tool_response,
    tool_rows,
)


@dataclass
class Harness:
    """One loop test rig: a service, its journal, and the fakes."""

    model: ScriptedModel
    tools: FakeToolExecutor
    journal: InMemoryRunJournal
    service: RunsService
    observer: RecordingObserver
    run: RunDTO | None = None
    config_id: UUID | None = None
    config: object = field(default=None)

    async def start(self) -> RunDTO:
        """Create the run that the test drives."""
        run = await self.service.create_run(RunCreateDTO(config_id=self.config_id))
        self.run = run
        return run

    async def process(self) -> RunDTO:
        """Run the loop over the created run."""
        assert self.run is not None
        return await self.service.process(self.run.id, observer=self.observer)

    async def messages(self):
        """Return the stored messages of the run."""
        assert self.run is not None
        return await self.journal.list_messages(self.run.id)


async def harness(
    responses,
    *,
    kill_after: str | None = None,
    config=None,
) -> Harness:
    """Build a harness with a scripted model and a fake executor."""
    model = ScriptedModel(responses)
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    agent = config or agent_config()
    await journal.save_config(agent)
    return Harness(
        model=model,
        tools=tools,
        journal=journal,
        service=service,
        observer=RecordingObserver(kill_after=kill_after),
        config_id=agent.id,
        config=agent,
    )


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_run_states_are_the_six_approved_states() -> None:
    """The state list holds six states and no stale pause state."""
    assert RUN_STATUSES == (
        "queued",
        "running",
        "awaiting_approval",
        "awaiting_background_job",
        "completed",
        "failed",
    )
    assert "stale_thread_paused" not in RUN_STATUSES


def test_allowed_transitions() -> None:
    """The state machine accepts the edges of the approved lifecycle."""
    edges = [
        ("queued", "running"),
        ("queued", "failed"),
        ("running", "awaiting_approval"),
        ("running", "awaiting_background_job"),
        ("running", "completed"),
        ("running", "failed"),
        ("awaiting_approval", "running"),
        ("awaiting_background_job", "running"),
    ]

    for source, target in edges:
        assert RunStateMachine.can_transition(source, target) is True
        assert RunStateMachine.require(source, target) == target


def test_removed_pause_state_has_no_edge() -> None:
    """The removed pause state cannot move anywhere, because it cannot exist."""
    assert RunStateMachine.can_transition("stale_thread_paused", "running") is False

    with pytest.raises(InvalidRunTransition):
        RunStateMachine.require("stale_thread_paused", "running")


def test_terminal_states_are_final() -> None:
    """A completed or a failed run never changes again."""
    assert TERMINAL_RUN_STATUSES == frozenset({"completed", "failed"})
    for terminal in TERMINAL_RUN_STATUSES:
        assert RunStateMachine.is_terminal(terminal) is True
        for target in RUN_STATUSES:
            assert RunStateMachine.can_transition(terminal, target) is False


@pytest.mark.asyncio
async def test_service_refuses_an_invalid_transition() -> None:
    """The service raises instead of writing a status jump."""
    service = RunsService(session=None, settings=Settings(APP_ENV="test"))

    run = await service.create_run(RunCreateDTO(config_id=uuid4()))
    await service.update_run_status(run.id, "running")
    await service.update_run_status(run.id, "completed")
    with pytest.raises(InvalidRunTransition):
        await service.update_run_status(run.id, "running")


# ---------------------------------------------------------------------------
# The partition policy
# ---------------------------------------------------------------------------


def test_policy_uses_the_explicit_level() -> None:
    """An approval policy level wins over the tool list."""
    policy = PartitionPolicy.from_config(
        agent_config(
            tools=("read_file", "compile_artifact", "gmail_send"),
            approval_policy={
                "compile_artifact": "background",
                "gmail_send": "external",
            },
        )
    )

    assert policy.partition_for("read_file") == "local"
    assert policy.partition_for("compile_artifact") == "background"
    assert policy.partition_for("gmail_send") == "external"


def test_policy_defaults_to_external_for_an_undeclared_tool() -> None:
    """A tool that the config does not declare can never run without a decision."""
    policy = PartitionPolicy.from_config(agent_config(tools=("read_file",)))

    assert policy.partition_for("rm_rf") == "external"


def test_policy_rejects_an_unknown_level_value() -> None:
    """A level outside the three partitions is an error, never a silent default."""
    with pytest.raises(ValidationError):
        PartitionPolicy.from_config(
            agent_config(tools=("echo",), approval_policy={"echo": "fast"})
        )


def test_policy_applies_an_explicit_level_outside_the_tool_list() -> None:
    """An explicit level still counts when the tool is not in the declared list."""
    policy = PartitionPolicy.from_config(
        agent_config(tools=("echo",), approval_policy={"gmail_send": "external"})
    )

    assert policy.partition_for("gmail_send") == "external"


# ---------------------------------------------------------------------------
# Tool call normalization
# ---------------------------------------------------------------------------


def test_tool_call_from_the_openai_shape() -> None:
    """The stored OpenAI shape converts back to a call with parsed arguments."""
    call = ToolCall.from_wire(
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
        }
    )

    assert call.id == "call-1"
    assert call.name == "read_file"
    assert call.arguments == {"path": "a.txt"}


def test_tool_call_from_the_neutral_shape() -> None:
    """A provider adapter can give the neutral shape."""
    call = ToolCall.from_wire(
        {"id": "call-2", "name": "bash", "arguments": {"command": "true"}}
    )

    assert call.arguments == {"command": "true"}
    assert call.to_wire()["function"]["name"] == "bash"


def test_tool_call_without_id_is_rejected() -> None:
    """A call without an id cannot be answered, so it is refused early."""
    with pytest.raises(ValueError):
        ToolCall.from_wire({"name": "read_file", "arguments": {}})


# ---------------------------------------------------------------------------
# The three way partition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partition_three_way() -> None:
    """One turn with a local, a background and an external call.

    The local call runs here, the background call becomes a job row, and the
    external call becomes a pending approval row. The run waits for the user.
    """
    h = await harness(
        [
            tool_response(
                ("call-local", "read_file", {"path": "a.txt"}),
                ("call-bg", "compile_artifact", {"name": "report"}),
                ("call-ext", "gmail_send", {"to": "user@example.test"}),
            )
        ]
    )
    await h.start()

    run = await h.process()

    assert run.status == "awaiting_approval"
    assert h.observer.stages == list(STAGES)
    assert h.model.calls and len(h.model.calls) == 1

    # One local call ran. It is the only tool result, and it is real output.
    assert h.tools.local == [("read_file", {"path": "a.txt"})]
    rows = tool_rows(await h.messages())
    assert [row.tool_call_id for row in rows] == ["call-local"]
    assert json.loads(rows[0].content or "{}")["content"] == "read_file ran"
    assert "enqueued" not in (rows[0].content or "")

    # One background job. It names the tool call and it is not a tool result.
    assert len(h.journal.jobs) == 1
    job = next(iter(h.journal.jobs.values()))
    assert job.type == BACKGROUND_JOB_TYPE
    assert job.status == "pending"
    assert job.payload["tool_call_id"] == "call-bg"
    assert job.payload["tool_name"] == "compile_artifact"
    assert job.payload["run_id"] == str(run.id)

    # One pending approval. The external tool did not run.
    approvals = await h.journal.list_approvals(run.id)
    assert len(approvals) == 1
    assert approvals[0].status == "pending"
    assert approvals[0].tool_call_id == "call-ext"
    assert approvals[0].tool_name == "gmail_send"
    assert h.tools.external == []


@pytest.mark.asyncio
async def test_background_call_is_a_state_not_a_fake_result() -> None:
    """A background call writes a job row and no tool row."""
    h = await harness(
        [tool_response(("call-bg", "compile_artifact", {"name": "report"}))]
    )
    await h.start()

    run = await h.process()

    assert run.status == "awaiting_background_job"
    assert tool_rows(await h.messages()) == []
    assert len(h.journal.jobs) == 1
    assert len(h.model.calls) == 1


@pytest.mark.asyncio
async def test_stage_order_for_one_local_turn() -> None:
    """One turn with one local call runs the six stages, then infers again."""
    h = await harness(
        [
            tool_response(("call-1", "read_file", {"path": "a.txt"})),
            text_response("The file says hello."),
        ]
    )
    await h.start()

    run = await h.process()

    assert run.status == "completed"
    assert h.observer.stages == [*STAGES, "assemble", "compact_check", "infer", "record"]
    assert len(h.model.calls) == 2
    assert [row.tool_call_id for row in tool_rows(await h.messages())] == ["call-1"]


@pytest.mark.asyncio
async def test_local_result_reaches_the_next_model_call() -> None:
    """The second model call receives the tool result of the first turn."""
    h = await harness(
        [
            tool_response(("call-1", "read_file", {"path": "a.txt"})),
            text_response("done"),
        ]
    )
    await h.start()

    await h.process()

    second = h.model.calls[1]["messages"]
    tool_messages = [row for row in second if row.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call-1"
    assert json.loads(tool_messages[0]["content"])["ok"] is True


@pytest.mark.asyncio
async def test_compact_check_records_a_request_over_the_limit() -> None:
    """Stage two records a compaction request when the context is too large."""
    h = await harness([text_response("done")])
    run = await h.start()
    await h.service.save_message(
        MessageCreateDTO(run_id=run.id, role="user", content="x" * 800)
    )
    loop = RunLoop(
        journal=h.journal,
        model=h.model,
        tools=h.tools,
        context_token_limit=10,
        compact_ratio=0.7,
    )

    finished = await loop.run(run.id)

    steps = await h.journal.list_steps(finished.id)
    requests = [
        step
        for step in steps
        if step.event_type == "thought"
        and step.payload.get("stage") == "compact_check"
    ]
    assert requests, "the compaction stage did not record a request"
    assert requests[0].payload["compaction_required"] is True
    assert requests[0].payload["estimated_tokens"] > 7


def test_token_estimate_grows_with_the_history() -> None:
    """The estimate is zero for an empty history and positive for a message."""
    from datetime import datetime, timezone

    from src.modules.runs.repository import MessageDTO

    assert estimate_tokens([]) == 0

    message = MessageDTO(
        id=uuid4(),
        run_id=None,
        session_id=None,
        role="user",
        content="x" * 400,
        tool_calls=None,
        tool_call_id=None,
        tool_name=None,
        tool_arguments=None,
        tool_result_reference=None,
        created_at=datetime.now(timezone.utc),
    )
    assert estimate_tokens([message]) >= 100
