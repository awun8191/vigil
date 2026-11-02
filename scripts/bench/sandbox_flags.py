"""Prove the sandbox flags against the live Docker daemon.

The script creates one container through the project's runner, reads the flags
back from the daemon with ``docker inspect``, and removes the container. The
runner decides the flags. This script only observes them, so it changes no
application behaviour.

Usage (from backend/):
    env -u PYTHONPATH uv run python ../scripts/bench/sandbox_flags.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.integrations.sandbox.sandbox import SandboxRunner  # noqa: E402

IMAGE = "python:3.14-slim"
# The daemon field paths, so every recorded value has an exact command.
FORMAT_PATHS = {
    "ReadonlyRootfs": ".HostConfig.ReadonlyRootfs",
    "CapDrop": ".HostConfig.CapDrop",
    "NetworkMode": ".HostConfig.NetworkMode",
    "Memory": ".HostConfig.Memory",
    "MemorySwap": ".HostConfig.MemorySwap",
    "PidsLimit": ".HostConfig.PidsLimit",
    "SecurityOpt": ".HostConfig.SecurityOpt",
    "NanoCpus": ".HostConfig.NanoCpus",
    "User": ".Config.User",
}


class BenchSettings(Settings):
    """Settings whose allowlist holds the one image this proof uses."""

    SANDBOX_IMAGE_ALLOWLIST: str = IMAGE


def inspect(container_id: str) -> dict[str, str]:
    """Read the policy fields back from the daemon."""
    output = {}
    for field, path in FORMAT_PATHS.items():
        result = subprocess.run(
            ["docker", "inspect", container_id, "--format", "{{" + path + "}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        output[field] = result.stdout.strip()
    tmpfs = subprocess.run(
        ["docker", "inspect", container_id, "--format", "{{json .HostConfig.Tmpfs}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    output["Tmpfs"] = tmpfs.stdout.strip()
    return output


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sandbox-bench-"))
    settings = BenchSettings(
        APP_ENV="test",
        WORKSPACE_BASE_PATH=str(root / "workspaces"),
        SANDBOX_IMAGE_ALLOWLIST=IMAGE,
    )
    runner = SandboxRunner(
        settings,
        workspace_base=root / "workspaces",
        artifact_dir=root / "artifacts",
    )
    if not await runner.is_healthy():
        print("Docker daemon is not reachable", file=sys.stderr)
        return 2
    if not await runner.image_available(IMAGE):
        print(f"{IMAGE} is not present locally", file=sys.stderr)
        return 2

    spec = runner.validate_spec(
        {"image": IMAGE, "command": ["sleep", "60"], "timeout_seconds": 120}
    )
    created = None
    started = time.perf_counter()
    try:
        created = await runner.create(spec, f"bench-{int(time.time())}")
        created_seconds = time.perf_counter() - started
        flags = inspect(created.container_id)
        result = await runner.execute_command(
            created.container_id, ["python3", "-c", "print('ok')"],
        )
    finally:
        total_seconds = time.perf_counter() - started
        if created is not None:
            await runner.stop_and_remove(created.container_id)

    report = {
        "image": IMAGE,
        "container_id": created.container_id if created else None,
        "create_seconds": created_seconds,
        "total_seconds": total_seconds,
        "inspect": flags,
        "exec_stdout": result.stdout.strip(),
        "exec_exit_code": result.exit_code,
        "args_argv": runner.container_args(spec).argv("docker", name="<name>"),
        "removed": True,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
