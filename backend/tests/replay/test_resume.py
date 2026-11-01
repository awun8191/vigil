"""Replay tests: a run must resume from rows after a pause or a kill.

Criterion 6 replays a paused run and checks that the model is called exactly
once more with four tool results. Criterion 7 kills the loop after stage five,
builds a new service over the same rows, and checks that the run still reaches
``completed``. The last case repeats the restart against a scratch Postgres
database, so the SQL journal is proven as well as the in-memory journal.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config.settings import Settings
from src.modules.runs.barrier import InvariantViolation
from src.modules.runs.repository import MessageCreateDTO, RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal
from src.schemas import Base
from tests.unit.loop_fakes import (
    FakeToolExecutor,
    LoopKilled,
    RecordingObserver,
    ScriptedModel,
    agent_config,
    text_response,
    tool_messages,
    tool_response,
    tool_rows,
)


def paused_rig(kill_after: str | None = None):
    """Build one run rig whose first turn holds a local, a job and an approval."""
    model = ScriptedModel(
        [
            tool_response(
                ("call-local", "read_file", {"path": "a.txt"}),
                ("call-bg", "compile_artifact", {"name": "report"}),
                ("call-ext", "gmail_send", {"to": "user@example.test"}),
            ),
            text_response("Everything is done."),
        ]
    )
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    observer = RecordingObserver(kill_after=kill_after)
    return model, tools, journal, service, observer


async def open_run(journal: InMemoryRunJournal, service: RunsService):
    """Register the agent config and create the queued run."""
    config = agent_config()
    await journal.save_config(config)
    return await service.create_run(RunCreateDTO(config_id=config.id))


async def settle(journal: InMemoryRunJournal, run_id) -> None:
    """Write the two rows that end the pause: a decision and a job result."""
    approvals = await journal.list_approvals(run_id)
    assert len(approvals) == 1
    await journal.decide_approval(
        approvals[0].id, status="approved", note="The user approved the send."
    )
    jobs = await journal.list_jobs(run_id)
    assert len(jobs) == 1
    await journal.complete_job(
        jobs[0].id, {"ok": True, "content": "report.pdf", "artifact_ref": "report.pdf"}
    )


@pytest.mark.asyncio
async def test_resume_after_all_results() -> None:
    """Replay a paused run: the model is called once more with four results.

    The first turn emits four tool calls: two local, one background and one
    external. The loop runs the two local calls, enqueues the job, requests the
    approval, and stops. The test then writes the decision and the job result,
    rebuilds the service, and resumes. The barrier must pass with four tool
    results, and the run must complete.
    """
    model = ScriptedModel(
        [
            tool_response(
                ("call-l1", "read_file", {"path": "a.txt"}),
                ("call-l2", "read_file", {"path": "b.txt"}),
                ("call-bg", "compile_artifact", {"name": "report"}),
                ("call-ext", "gmail_send", {"to": "user@example.test"}),
            ),
            text_response("All four results are in."),
        ]
    )
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    first = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    config = agent_config()
    await journal.save_config(config)
    run = await first.create_run(RunCreateDTO(config_id=config.id))

    paused = await first.process(run.id)

    assert paused.status == "awaiting_approval"
    assert len(model.calls) == 1
    assert len(tool_rows(await journal.list_messages(run.id))) == 2
    assert len(await journal.list_jobs(run.id)) == 1
    assert len(await journal.list_approvals(run.id)) == 1

    await settle(journal, run.id)

    # A new service instance holds no state from the paused run.
    second = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    finished = await second.resume_run(run.id)

    assert finished.status == "completed"
    assert len(model.calls) == 2, "the model must be called exactly once more"

    results = tool_messages(model.calls[1])
    assert len(results) == 4, "the model must receive four tool results"
    assert [row["tool_call_id"] for row in results] == [
        "call-l1",
        "call-l2",
        "call-bg",
        "call-ext",
    ]

    rows = tool_rows(await journal.list_messages(run.id))
    assert [row.tool_call_id for row in rows] == [
        "call-l1",
        "call-l2",
        "call-bg",
        "call-ext",
    ]
    assert json.loads(rows[2].content or "{}")["artifact_ref"] == "report.pdf"
    assert json.loads(rows[3].content or "{}")["content"] == "gmail_send sent"

    # No tool ran twice: two local calls before the pause, none after it.
    assert tools.local_names() == ["read_file", "read_file"]
    assert tools.external_names() == ["gmail_send"]


@pytest.mark.asyncio
async def test_resume_after_restart() -> None:
    """Kill the loop after stage five, rebuild the service, and resume.

    The kill lands after dispatch and before record. The rows hold the assistant
    message, the local tool result, the job and the approval. A new service
    rebuilds every stage from those rows and the run reaches completed.
    """
    model, tools, journal, first, observer = paused_rig(kill_after="dispatch")
    run = await open_run(journal, first)

    with pytest.raises(LoopKilled) as killed:
        await first.process(run.id, observer=observer)

    assert killed.value.stage == "dispatch"
    assert observer.stages[-1] == "dispatch"
    assert "record" not in observer.stages

    # The write of stage five survived the kill.
    assert len(tools.local) == 1
    assert len(tool_rows(await journal.list_messages(run.id))) == 1
    assert len(await journal.list_jobs(run.id)) == 1
    assert len(await journal.list_approvals(run.id)) == 1

    await settle(journal, run.id)

    second = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    recovered = await second.resume_run(run.id)

    assert recovered.status == "completed"
    assert recovered.ended_at is not None
    assert len(model.calls) == 2
    assert len(tool_rows(await journal.list_messages(run.id))) == 3
    # The local tool did not run a second time after the restart.
    assert tools.local_names() == ["read_file"]
    assert tools.external_names() == ["gmail_send"]
    assert len(tool_messages(model.calls[1])) == 3


async def drive(journal: InMemoryRunJournal, service: RunsService, run_id, limit: int = 6):
    """Resume a run until it stops, settling each pause as the user would."""
    run = await service.resume_run(run_id)
    for _ in range(limit):
        if run.status in ("completed", "failed"):
            return run
        await settle(journal, run_id)
        run = await service.resume_run(run_id)
    raise AssertionError(f"the run did not stop: {run.status}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["assemble", "compact_check", "infer", "partition", "dispatch", "record"]
)
async def test_resume_after_a_kill_at_any_stage(stage: str) -> None:
    """A kill after any of the six stages still resumes to completed.

    The stage observer raises after the named stage. A new service then reads
    the rows and drives the run to completion, and the test settles each pause
    with a decision and a job result. In every case the local tool runs exactly
    once in total, and the model is called exactly twice.
    """
    model, tools, journal, first, observer = paused_rig(kill_after=stage)
    run = await open_run(journal, first)

    with pytest.raises(LoopKilled):
        await first.process(run.id, observer=observer)

    assert observer.stages[-1] == stage

    second = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    recovered = await drive(journal, second, run.id)

    assert recovered.status == "completed"
    assert len(model.calls) == 2
    assert tools.local_names() == ["read_file"]
    assert tools.external_names() == ["gmail_send"]
    assert len(tool_rows(await journal.list_messages(run.id))) == 3


@pytest.mark.asyncio
async def test_resume_does_not_repeat_a_final_answer() -> None:
    """A kill after the infer stage does not spend a second model call.

    The infer stage ends only after the assistant message is a row. When that
    row holds the final text and no tool call, the stack resumes the run with no
    model call at all.
    """
    model = ScriptedModel([text_response("The answer is 42.")])
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    config = agent_config()
    await journal.save_config(config)
    run = await service.create_run(RunCreateDTO(config_id=config.id))

    with pytest.raises(LoopKilled):
        await service.process(run.id, observer=RecordingObserver(kill_after="infer"))

    assert len(model.calls) == 1
    stored = await journal.get_run(run.id)
    assert stored is not None
    assert stored.status == "running"

    second = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    finished = await second.resume_run(run.id)

    assert finished.status == "completed"
    assert len(model.calls) == 1
    assert len(await journal.list_messages(run.id)) == 1


@pytest.mark.asyncio
async def test_barrier_blocks_the_model_call() -> None:
    """A duplicated tool row stops the loop before the next model call."""
    model, tools, journal, service, observer = paused_rig()
    run = await open_run(journal, service)

    await service.process(run.id)
    assert len(model.calls) == 1

    # A double write leaves two rows for one tool call id.
    existing = tool_rows(await journal.list_messages(run.id))[0]
    await journal.append_message(
        MessageCreateDTO(
            role="tool",
            run_id=run.id,
            content=existing.content,
            tool_call_id=existing.tool_call_id,
            tool_name=existing.tool_name,
        )
    )

    await settle(journal, run.id)
    fresh = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )

    with pytest.raises(InvariantViolation) as caught:
        await fresh.resume_run(run.id)

    assert caught.value.duplicate == ("call-local",)
    assert len(model.calls) == 1, "the barrier must stop before the model call"

    failed = await journal.get_run(run.id)
    assert failed is not None
    assert failed.status == "failed"


@pytest.mark.asyncio
async def test_resume_after_restart_in_database() -> None:
    """Repeat the restart against a scratch Postgres database.

    The first service writes through the SQL journal. The test then closes the
    session, disposes the engine, and builds a new service on a new connection.
    The rows are the only carrier of the run, so the resume proves the database
    path.
    """
    settings = Settings(APP_ENV="test")
    admin_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_url = settings.DATABASE_URL.rsplit("/", 1)[0] + f"/v06_loop_{uuid4().hex[:12]}"
    db_name = db_url.rsplit("/", 1)[1]

    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await admin.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    async with admin.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    model = ScriptedModel(
        [
            tool_response(
                ("call-local", "read_file", {"path": "a.txt"}),
                ("call-bg", "compile_artifact", {"name": "report"}),
                ("call-ext", "gmail_send", {"to": "user@example.test"}),
            ),
            text_response("Everything is done."),
        ]
    )
    tools = FakeToolExecutor()

    try:
        session = AsyncSession(engine, expire_on_commit=False)
        first = RunsService(
            session=session, settings=settings, llm=model, tools=tools
        )
        config = agent_config()
        await first.save_agent_config(config)
        run = await first.start_run(config.id)
        assert run.status == "queued"

        queued_jobs = await first.journal.list_jobs(run.id)
        assert [job.type for job in queued_jobs] == ["execute_run"]
        assert queued_jobs[0].priority == 90

        observer = RecordingObserver(kill_after="dispatch")
        with pytest.raises(LoopKilled):
            await first.process(run.id, observer=observer)

        assert len(tools.local) == 1
        await session.close()
        await engine.dispose()

        # The process is gone. Only the database remains.
        engine_again = create_async_engine(db_url)
        session_again = AsyncSession(engine_again, expire_on_commit=False)
        second = RunsService(
            session=session_again, settings=settings, llm=model, tools=tools
        )
        try:
            stored = await second.get_run_by_id(run.id)
            assert stored is not None
            assert stored.status == "running"
            assert len(await second.get_messages_for_run(run.id)) == 2

            approvals = await second.journal.list_approvals(run.id)
            await second.journal.decide_approval(
                approvals[0].id, status="approved", note="approved"
            )
            tool_jobs = [
                job
                for job in await second.journal.list_jobs(run.id)
                if job.payload.get("kind") == "tool_call"
            ]
            assert len(tool_jobs) == 1
            await second.journal.complete_job(
                tool_jobs[0].id, {"ok": True, "content": "report.pdf"}
            )

            finished = await second.resume_run(run.id)

            assert finished.status == "completed"
            assert len(model.calls) == 2
            assert len(tool_messages(model.calls[1])) == 3
            assert tools.local_names() == ["read_file"]
            assert tools.external_names() == ["gmail_send"]
        finally:
            await session_again.close()
            await engine_again.dispose()
    finally:
        async with admin.connect() as conn:
            await conn.execute(
                sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            )
        await admin.dispose()
