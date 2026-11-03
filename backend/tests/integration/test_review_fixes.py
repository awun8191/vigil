"""Integration tests verifying the code review fixes and live muse-spark-1.3-contributor execution."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.dependencies import RunToolExecutor, get_mcp_host
from src.integrations.llm.client import LLMClient
from src.modules.artifacts.repository import ArtifactCreateDTO
from src.modules.artifacts.service import ArtifactsService
from src.modules.compaction.service import CompactionService
from src.modules.connectors.service import ConnectorsService
from src.modules.jobs.repository import JobDTO
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    RunCreateDTO,
)
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal, InvalidRunTransition
from src.modules.runs.tools import openai_tool_declarations
from src.worker import JobContext, dispatch_job


@pytest.mark.asyncio
async def test_live_muse_spark_run_loop_with_tool_call() -> None:
    """Drive a multi-turn RunLoop against the live Meta Model API (muse-spark-1.3-contributor)."""
    settings = Settings()
    if not settings.REASONING_LLM_API_KEY:
        pytest.skip("REASONING_LLM_API_KEY is not configured in .env")

    assert settings.REASONING_LLM_MODEL == "muse-spark-1.3-contributor"
    assert settings.APP_ENV == "development"

    llm = LLMClient(settings)
    journal = InMemoryRunJournal()

    executed_calls: list[tuple[str, dict]] = []

    class LocalWorkspaceExecutor:
        async def execute(self, name: str, arguments: dict) -> dict:
            executed_calls.append((name, dict(arguments)))
            return {
                "ok": True,
                "content": "SECRET_CODE=VIGIL-2026-OK",
                "artifact_ref": None,
                "error": None,
            }

        async def execute_external(self, name: str, arguments: dict) -> dict:
            return {"ok": True, "content": "ok", "artifact_ref": None, "error": None}

    service = RunsService(
        session=None,
        settings=settings,
        llm=llm,
        journal=journal,
        tools=LocalWorkspaceExecutor(),  # type: ignore[arg-type]
    )
    config = await service.save_agent_config(
        AgentConfigDTO(
            id=uuid4(),
            name="muse-spark-live-agent",
            system_prompt=(
                "You are a concise assistant. Use the `read` tool to read /workspace/code.txt "
                "when asked, then report the exact value of SECRET_CODE."
            ),
            model=settings.REASONING_LLM_MODEL,
            tools=["read"],
            skills=[],
            memory_scopes=[],
            approval_policy={"read": "local"},
            limits={},
        )
    )
    run = await service.create_run(RunCreateDTO(config_id=config.id, status="running"))
    await service.save_message(
        MessageCreateDTO(
            run_id=run.id,
            role="user",
            content="Read /workspace/code.txt using the read tool and tell me the SECRET_CODE.",
        )
    )

    schemas = openai_tool_declarations(["read"])
    loop = service.build_loop(max_iterations=5, tool_schemas=schemas)
    final_run = await loop.run(run.id)

    assert final_run.status == "completed"
    assert len(executed_calls) >= 1
    assert executed_calls[0][0] == "read"
    messages = await journal.list_messages(run.id)
    assert messages[-1].role == "assistant"
    assert "VIGIL-2026-OK" in (messages[-1].content or "")


@pytest.mark.asyncio
async def test_compaction_service_wired_into_run_loop() -> None:
    """Verify CompactionService compacts MessageDTO rows and updates model_messages in RunLoop."""
    settings = Settings(APP_ENV="test")
    journal = InMemoryRunJournal()
    compactor = CompactionService(session=None, settings=settings)

    seen_model_messages: list[list[dict]] = []

    class InspectingLLM:
        async def complete(self, messages, tools=None, temperature=0.0, tier="reasoning", max_tokens=None):
            from src.integrations.repositories import LLMResponseDTO

            seen_model_messages.append(list(messages))
            return LLMResponseDTO(
                content="Done after compaction.",
                tool_calls=[],
                prompt_tokens=20,
                completion_tokens=5,
                model="test-model",
            )

    service = RunsService(
        session=None,
        settings=settings,
        llm=InspectingLLM(),  # type: ignore[arg-type]
        journal=journal,
        compactor=compactor,
    )
    config = await service.save_agent_config(
        AgentConfigDTO(
            id=uuid4(),
            name="compaction-wired-agent",
            system_prompt="System anchor.",
            model="test-model",
            tools=[],
            skills=[],
            memory_scopes=[],
            approval_policy={},
            limits={},
        )
    )
    run = await service.create_run(RunCreateDTO(config_id=config.id, status="running"))
    await service.save_message(
        MessageCreateDTO(run_id=run.id, role="user", content="Goal: summarize everything.")
    )
    await service.save_message(
        MessageCreateDTO(
            run_id=run.id,
            role="assistant",
            content=" Intermediate commentary " * 200,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        )
    )
    await service.save_message(
        MessageCreateDTO(
            run_id=run.id,
            role="tool",
            tool_call_id="call_1",
            tool_name="bash",
            content="file_output_line\n" * 400,
        )
    )

    loop = service.build_loop(max_iterations=3)
    loop.context_token_limit = 200
    loop.compact_ratio = 0.5

    final_run = await loop.run(run.id)
    assert final_run.status == "completed"
    assert len(seen_model_messages) == 1


@pytest.mark.asyncio
async def test_run_tool_executor_snapshots_and_restores_workspace(tmp_path: Path) -> None:
    """Verify RunToolExecutor snapshots /workspace on close() and restores it on the next start()."""
    settings = Settings(APP_ENV="test", WORKSPACE_BASE_PATH=str(tmp_path))
    run_id = uuid4()
    events: list[tuple[str, str, str]] = []

    class RecordingSandbox:
        async def create_container(self, run_id: str, timeout_seconds: int = 300):
            from src.integrations.repositories import SandboxContainerDTO

            return SandboxContainerDTO(
                container_id=f"cid-{len(events)}",
                workspace_path="/workspace",
                created_at=1000.0,
            )

        async def snapshot_workspace(self, container_id: str, target_tar_path: str) -> int:
            Path(target_tar_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_tar_path).write_bytes(b"tar-archive")
            events.append(("snapshot", container_id, target_tar_path))
            return 11

        async def restore_workspace(self, container_id: str, source_tar_path: str) -> None:
            events.append(("restore", container_id, source_tar_path))

        async def stop_and_remove(self, container_id: str) -> None:
            events.append(("stop", container_id, ""))

    sandbox = RecordingSandbox()
    mcp_host = get_mcp_host(settings)

    exec1 = RunToolExecutor(
        session=None, settings=settings, run_id=run_id, sandbox=sandbox, mcp_host=mcp_host  # type: ignore[arg-type]
    )
    cid1 = await exec1.start()
    await exec1.close()

    exec2 = RunToolExecutor(
        session=None, settings=settings, run_id=run_id, sandbox=sandbox, mcp_host=mcp_host  # type: ignore[arg-type]
    )
    cid2 = await exec2.start()
    await exec2.close()

    assert [e[0] for e in events] == ["snapshot", "stop", "restore", "snapshot", "stop"]
    assert events[0][1] == cid1
    assert events[2][1] == cid2


@pytest.mark.asyncio
async def test_discover_mcp_tools_worker_handler_and_host_execution(tmp_path: Path) -> None:
    """Verify the worker dispatches discover_mcp_tools and MCPHostClient executes discovered tools."""
    script = textwrap.dedent(
        """\
        import sys, json
        for line in sys.stdin:
            msg = json.loads(line)
            method = msg.get("method")
            if method == "initialize":
                res = {"server": "test-mcp", "version": "1.0"}
            elif method == "tools/list":
                res = {"tools": [{"name": "echo_ext", "description": "Echo", "input_schema": {"type": "object"}}]}
            elif method == "tools/call":
                args = msg.get("params", {}).get("arguments", {})
                res = {"content": [{"type": "text", "text": f"ECHO:{args.get('msg', '')}"}], "isError": False}
            else:
                res = {}
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": res}) + "\\n")
            sys.stdout.flush()
        """
    )
    server_file = tmp_path / "mcp_echo.py"
    server_file.write_text(script, encoding="utf-8")
    command = f"{sys.executable} {server_file}"

    settings = Settings(APP_ENV="test")
    ConnectorsService._mcp_servers.clear()
    ConnectorsService._mcp_servers_by_name.clear()
    ConnectorsService._mcp_tools.clear()

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    job = JobDTO(
        id=uuid4(),
        type="discover_mcp_tools",
        payload={
            "name": "echo-server",
            "transport": "stdio",
            "command": command,
            "url": None,
        },
        run_at=now,
        attempts=1,
        max_attempts=3,
        status="locked",
        locked_by="worker-1",
        locked_at=now,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    await dispatch_job(job, JobContext(settings=settings, session=None))

    host = get_mcp_host(settings)
    tools = await host.list_tools("echo-server")
    assert [t.name for t in tools] == ["echo_ext"]

    result = await host.execute_tool("echo-server", "echo_ext", {"msg": "hello"})
    assert result.is_error is False
    assert result.output == "ECHO:hello"


@pytest.mark.asyncio
async def test_terminal_run_rejects_stale_status_overwrite() -> None:
    """Verify a cancelled/failed run cannot be overwritten back to completed by a stale worker."""
    journal = InMemoryRunJournal()
    run = await journal.create_run(RunCreateDTO(config_id=uuid4(), status="running"))
    await journal.update_run(run.id, status="failed")

    with pytest.raises(InvalidRunTransition):
        await journal.update_run(run.id, status="completed")


@pytest.mark.asyncio
async def test_artifacts_service_streams_real_file_bytes(tmp_path: Path) -> None:
    """Verify ArtifactsService streams the actual file bytes from disk."""
    settings = Settings(APP_ENV="test")
    service = ArtifactsService(session=None, settings=settings)  # type: ignore[arg-type]
    artifact_file = tmp_path / "report.txt"
    artifact_file.write_bytes(b"real-artifact-payload-bytes")

    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="file",
            path=str(artifact_file),
            size_bytes=artifact_file.stat().st_size,
            preview="real-artifact",
        )
    )
    stream, filename, _media_type, size = await service.get_artifact_stream(saved.id)
    chunks = [chunk async for chunk in stream]
    assert b"".join(chunks) == b"real-artifact-payload-bytes"
    assert filename == "report.txt"
    assert size == len(b"real-artifact-payload-bytes")
