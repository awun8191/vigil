#!/usr/bin/env python3
"""Interactive CLI harness to try out the Vigil agent runtime and 6-stage RunLoop.

Usage (interactive REPL):
    cd backend && uv run python scripts/try_agent.py

Usage (single prompt):
    cd backend && uv run python scripts/try_agent.py --prompt "Write a fib.py script in /workspace, run it with bash, and tell me the 10th Fibonacci number."
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import UUID, uuid4

# Ensure `src` is importable when invoked as `uv run python scripts/try_agent.py`
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.config.settings import Settings
from src.integrations.llm.client import LLMClient
from src.integrations.repositories import (
    SandboxContainerDTO,
    SandboxExecResultDTO,
)
from src.modules.compaction.service import CompactionService
from src.modules.runs.core_context import (
    DEFAULT_BUNDLED_SKILLS,
    build_core_system_prompt,
)
from src.modules.runs.loop import LoopLimitExceeded
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    RunCreateDTO,
)
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal
from src.modules.runs.tools import (
    TOOLS,
    ToolContext,
    invoke_tool,
    openai_tool_declarations,
)


# ANSI colors for terminal output
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
RESET = "\033[0m"


class LocalWorkspaceSandboxRunner:
    """Host-backed /workspace sandbox runner for fast local CLI exploration without Docker."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, path: str) -> Path:
        raw = path.strip()
        if raw.startswith("/workspace/"):
            rel = raw[len("/workspace/") :]
        elif raw == "/workspace":
            rel = "."
        else:
            rel = raw.lstrip("/")
        candidate = (self.workspace_root / rel).resolve()
        if candidate != self.workspace_root and self.workspace_root not in candidate.parents:
            raise ValueError(f"Path {path!r} escapes workspace {self.workspace_root}")
        return candidate

    async def create_container(
        self, run_id: str, timeout_seconds: int = 900
    ) -> SandboxContainerDTO:
        return SandboxContainerDTO(
            container_id=f"local-{run_id[:8]}",
            workspace_path=str(self.workspace_root),
            created_at=time.time(),
        )

    async def execute_command(
        self,
        container_id: str,
        command: str,
        timeout_seconds: int = 60,
    ) -> SandboxExecResultDTO:
        t0 = time.monotonic()
        # Replace /workspace references with the local workspace path
        rewritten = command.replace("/workspace", str(self.workspace_root))
        env = {
            **os.environ,
            "CI": "true",
            "NPM_CONFIG_YES": "true",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        proc = await asyncio.create_subprocess_shell(
            rewritten,
            cwd=str(self.workspace_root),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            stdout, stderr = b"", f"Command timed out after {timeout_seconds}s".encode()
            exit_code = 124
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if getattr(proc, "_transport", None) is not None:
                proc._transport.close()  # type: ignore[union-attr]
        duration_ms = (time.monotonic() - t0) * 1000.0
        return SandboxExecResultDTO(
            exit_code=exit_code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )

    async def read_file(self, container_id: str, path: str) -> bytes:
        target = self._resolve_safe_path(path)
        return target.read_bytes()

    async def write_file(self, container_id: str, path: str, content: bytes) -> None:
        target = self._resolve_safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def snapshot_workspace(self, container_id: str, target_tar_path: str) -> int:
        return 0

    async def restore_workspace(self, container_id: str, source_tar_path: str) -> None:
        pass

    async def stop_and_remove(self, container_id: str) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True


class CLIExecutor:
    """Tool executor that prints tool calls and results live in the terminal."""

    def __init__(self, runner: LocalWorkspaceSandboxRunner, delivery_dir: Path) -> None:
        self.runner = runner
        self.delivery_dir = delivery_dir
        self.delivery_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        print(f"  {YELLOW}⚙ Tool Call (local):{RESET} {BOLD}{name}{RESET}({ dict(arguments) })")
        context = ToolContext(
            runner=self.runner,
            container_id="local-cli",
            delivery_dir=self.delivery_dir,
        )
        result = await invoke_tool(name, dict(arguments), context)
        status_str = f"{GREEN}OK{RESET}" if result.ok else f"{RED}ERR{RESET}"
        preview = str(result.content if result.ok else result.error)
        if len(preview) > 300:
            preview = preview[:300] + "... [truncated]"
        print(f"  {DIM}↳ [{status_str}{DIM}] {preview}{RESET}")
        return result.model_dump()

    async def execute_external(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        print(f"  {MAGENTA}⚙ Tool Call (external approved):{RESET} {BOLD}{name}{RESET}({ dict(arguments) })")
        if name == "ask_user":
            question = arguments.get("question", "Input required:")
            answer = input(f"\n{BOLD}{CYAN}❓ Agent asks:{RESET} {question}\n{BOLD}Your answer > {RESET}")
            return {"ok": True, "content": answer, "artifact_ref": None, "error": None}
        return await self.execute(name, arguments)


class CLIStageObserver:
    """Prints the 6-stage RunLoop transitions in real time."""

    async def on_stage(self, run_id: UUID, stage: str) -> None:
        print(f"  {DIM}[stage] {stage}{RESET}")


async def drive_until_done(
    service: RunsService,
    journal: InMemoryRunJournal,
    run_id: UUID,
    tool_schemas: list[dict[str, Any]],
    auto_approve: bool = False,
    max_iterations: int = 60,
) -> None:
    """Drive the RunLoop, interactively prompting for approvals or iteration extensions."""
    loop = service.build_loop(
        observer=CLIStageObserver(),
        max_iterations=max_iterations,
        tool_schemas=tool_schemas,
    )
    while True:
        try:
            run = await loop.run(run_id)
        except LoopLimitExceeded:
            print(
                f"\n{YELLOW}{BOLD}⚡ Iteration Limit Reached:{RESET} "
                f"Agent is still working after {max_iterations} iterations."
            )
            if auto_approve:
                cont = "y"
            else:
                cont = input(
                    f"{BOLD}Continue for another {max_iterations} iterations? [Y/n] > {RESET}"
                ).strip().lower()
            if cont in ("", "y", "yes"):
                continue
            await journal.update_run(run_id, status="completed")
            break

        if run.status == "awaiting_approval":
            approvals = [
                a for a in await journal.list_approvals(run_id) if a.status == "pending"
            ]
            for approval in approvals:
                print(
                    f"\n{YELLOW}{BOLD}🔒 Approval Required:{RESET} "
                    f"Tool {BOLD}{approval.tool_name}{RESET} with args {approval.arguments}"
                )
                if auto_approve:
                    choice = "y"
                    print(f"{GREEN}Auto-approving (--yes){RESET}")
                else:
                    choice = input(f"{BOLD}Approve execution? [y/N] > {RESET}").strip().lower()
                decision = "approved" if choice in ("y", "yes") else "rejected"
                await journal.decide_approval(
                    approval.id,
                    status=decision,  # type: ignore[arg-type]
                    note="Decided via CLI harness",
                    decided_at=datetime.now(timezone.utc),
                )
            await journal.update_run(run_id, status="running")
            continue
        break


async def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive CLI harness for the Vigil Agent")
    parser.add_argument("--prompt", "-p", type=str, help="Run a single prompt and exit")
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default="/tmp/vigil-cli-workspace",
        help="Host directory mapped as /workspace for the agent (default: /tmp/vigil-cli-workspace)",
    )
    parser.add_argument(
        "--require-bash-approval",
        action="store_true",
        help="Gate `bash` tool behind interactive human approval to test the approval pause/resume flow",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Automatically approve external tool requests",
    )
    parser.add_argument(
        "--max-iterations",
        "-m",
        type=int,
        default=60,
        help="Max iterations per prompt before asking to continue (default: 60)",
    )
    args = parser.parse_args()

    settings = Settings()
    workspace_path = Path(args.workspace).resolve()
    delivery_path = workspace_path / "_delivered"
    runner = LocalWorkspaceSandboxRunner(workspace_path)
    executor = CLIExecutor(runner, delivery_path)
    journal = InMemoryRunJournal()
    llm = LLMClient(settings)
    compactor = CompactionService(session=None, settings=settings)

    service = RunsService(
        session=None,
        settings=settings,
        llm=llm,
        journal=journal,
        tools=executor,  # type: ignore[arg-type]
        compactor=compactor,
    )

    tools = list(TOOLS.keys())
    skills = list(DEFAULT_BUNDLED_SKILLS)
    approval_policy = {t: "local" for t in tools}
    approval_policy["ask_user"] = "external"
    if args.require_bash_approval:
        approval_policy["bash"] = "external"

    config = await service.save_agent_config(
        AgentConfigDTO(
            id=uuid4(),
            name="vigil-cli-agent",
            system_prompt=build_core_system_prompt(skills=skills),
            model=settings.REASONING_LLM_MODEL,
            tools=tools,
            skills=skills,
            memory_scopes=["global"],
            approval_policy=approval_policy,
            limits={},
        )
    )
    tool_schemas = openai_tool_declarations(tools)

    print(f"{BOLD}{CYAN}╭──────────────────────────────────────────────────────────╮{RESET}")
    print(f"{BOLD}{CYAN}│                Vigil Agent CLI Harness                   │{RESET}")
    print(f"{BOLD}{CYAN}╰──────────────────────────────────────────────────────────╯{RESET}")
    print(f"  {BOLD}Model:{RESET}     {settings.REASONING_LLM_MODEL} ({settings.REASONING_LLM_BASE_URL})")
    print(f"  {BOLD}API Key:{RESET}   {'configured (' + settings.REASONING_LLM_API_KEY[:12] + '...)' if settings.REASONING_LLM_API_KEY else RED + 'NOT SET (using fallback)' + RESET}")
    print(f"  {BOLD}Workspace:{RESET} {workspace_path} (mapped as /workspace)")
    print(f"  {BOLD}Tools:{RESET}     {', '.join(tools)}")
    print(f"  {BOLD}Skills:{RESET}    {', '.join(skills)}\n")

    run = await service.create_run(RunCreateDTO(config_id=config.id, status="running"))

    if args.prompt:
        await service.save_message(
            MessageCreateDTO(run_id=run.id, role="user", content=args.prompt)
        )
        await drive_until_done(
            service,
            journal,
            run.id,
            tool_schemas,
            auto_approve=args.yes,
            max_iterations=args.max_iterations,
        )
        messages = await journal.list_messages(run.id)
        final_run = await journal.get_run(run.id)
        if messages and messages[-1].role == "assistant":
            print(f"\n{BOLD}{GREEN}Agent >{RESET} {messages[-1].content}\n")
        if final_run:
            print(f"{DIM}Tokens: {final_run.token_usage} | Status: {final_run.status}{RESET}")
        return

    print(f"{DIM}Type your message and press Enter. Commands: /steps, /files, /new, /exit{RESET}\n")
    while True:
        try:
            user_input = input(f"{BOLD}{CYAN}You > {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input in ("/exit", "/quit", "exit", "quit"):
            break
        if user_input == "/new":
            run = await service.create_run(RunCreateDTO(config_id=config.id, status="running"))
            print(f"{GREEN}Started new run {run.id}{RESET}\n")
            continue
        if user_input == "/files":
            files = sorted(
                str(p.relative_to(workspace_path))
                for p in workspace_path.rglob("*")
                if p.is_file()
            )
            print(f"{BOLD}Workspace files:{RESET} {files or '(empty)'}\n")
            continue
        if user_input == "/steps":
            steps = await journal.list_steps(run.id)
            for s in steps:
                print(f"  #{s.step_number} [{s.event_type}] {s.payload}")
            print()
            continue

        # Re-open completed run for multi-turn conversation in the same thread
        current = await journal.get_run(run.id)
        if current and current.status in ("completed", "failed"):
            run = await service.create_run(RunCreateDTO(config_id=config.id, status="running"))
            for prev_msg in await journal.list_messages(current.id):
                await journal.append_message(
                    MessageCreateDTO(
                        run_id=run.id,
                        role=prev_msg.role,
                        content=prev_msg.content,
                        tool_calls=prev_msg.tool_calls,
                        tool_call_id=prev_msg.tool_call_id,
                        tool_name=prev_msg.tool_name,
                        tool_arguments=prev_msg.tool_arguments,
                        tool_result_reference=prev_msg.tool_result_reference,
                    )
                )

        await service.save_message(
            MessageCreateDTO(run_id=run.id, role="user", content=user_input)
        )
        await drive_until_done(
            service,
            journal,
            run.id,
            tool_schemas,
            auto_approve=args.yes,
            max_iterations=args.max_iterations,
        )
        messages = await journal.list_messages(run.id)
        final_run = await journal.get_run(run.id)
        if messages and messages[-1].role == "assistant":
            print(f"\n{BOLD}{GREEN}Agent >{RESET} {messages[-1].content}\n")
        if final_run:
            print(f"{DIM}Tokens: {final_run.token_usage} | Status: {final_run.status}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
