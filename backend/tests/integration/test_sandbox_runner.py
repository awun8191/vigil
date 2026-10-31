"""Integration tests for the Docker sandbox runner.

These tests need a Docker daemon and a local sandbox image. They skip with a clear reason
when either is absent, so a machine without Docker still gets a green suite.

The image is taken from ``VIRGIL_TEST_SANDBOX_IMAGE`` when it is set. Otherwise the first
candidate image that is present in the local daemon store is used.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Sequence
from uuid import uuid4

import pytest
import pytest_asyncio

from src.config.settings import Settings
from src.integrations.sandbox.archive import verify_archive
from src.integrations.sandbox.contract import MAX_STREAM_BYTES, SandboxSpec
from src.integrations.sandbox.sandbox import SandboxRunner, docker_binary_available

TEST_IMAGE_ENV = "VIRGIL_TEST_SANDBOX_IMAGE"
CANDIDATE_IMAGES: tuple[str, ...] = (
    "python:3.14-slim",
    "python:3.13-slim",
    "python:3.12-slim",
    "python:3-slim",
)
SLEEP_COMMAND: tuple[str, ...] = ("sleep", "600")


class IntegrationSandboxSettings(Settings):
    """Settings whose allowlist covers every candidate test image."""

    SANDBOX_IMAGE_ALLOWLIST: str = ",".join(CANDIDATE_IMAGES)


@dataclass
class SandboxHarness:
    """A ready runner plus the image the tests may use."""

    runner: SandboxRunner
    image: str
    root: Path

    def spec(self, command: Sequence[str], **extra: object) -> SandboxSpec:
        """Validate a spec for the test image."""
        payload: dict[str, object] = {"image": self.image, "command": list(command)}
        payload.update(extra)
        return self.runner.validate_spec(payload)

    @asynccontextmanager
    async def container(self, command: Sequence[str] = SLEEP_COMMAND) -> AsyncIterator[str]:
        """Create a container and always remove it."""
        created = await self.runner.create(self.spec(command), f"int-{uuid4().hex[:10]}")
        try:
            yield created.container_id
        finally:
            await self.runner.stop_and_remove(created.container_id)


@pytest_asyncio.fixture
async def sandbox(tmp_path: Path) -> AsyncIterator[SandboxHarness]:
    """Provide a runner, or skip with a clear reason when Docker is not usable."""
    if not docker_binary_available():
        pytest.skip("Docker CLI is not installed, so the sandbox runner cannot be exercised")

    settings = IntegrationSandboxSettings(
        APP_ENV="test",
        WORKSPACE_BASE_PATH=str(tmp_path / "workspaces"),
    )
    runner = SandboxRunner(
        settings,
        workspace_base=tmp_path / "workspaces",
        artifact_dir=tmp_path / "artifacts",
    )
    if not await runner.is_healthy():
        pytest.skip("Docker daemon is not reachable, so the sandbox runner cannot be exercised")

    requested = os.environ.get(TEST_IMAGE_ENV, "").strip()
    candidates = (requested,) + CANDIDATE_IMAGES if requested else CANDIDATE_IMAGES
    for candidate in candidates:
        if await runner.image_available(candidate):
            yield SandboxHarness(runner=runner, image=candidate, root=tmp_path)
            return
    pytest.skip(
        "no sandbox test image is available locally; pull one of "
        f"{', '.join(candidates)} or set {TEST_IMAGE_ENV}"
    )


@pytest.mark.asyncio
async def test_is_healthy_reports_daemon(sandbox: SandboxHarness) -> None:
    """The runner sees the daemon it will use."""
    assert await sandbox.runner.is_healthy() is True


@pytest.mark.asyncio
async def test_create_exec_and_remove(sandbox: SandboxHarness) -> None:
    """A validated spec creates a container, runs argv, and the container is removed."""
    async with sandbox.container() as container_id:
        assert await sandbox.runner.is_healthy() is True
        result = await sandbox.runner.execute_command(
            container_id,
            ["python3", "-c", "print('inside the sandbox')"],
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "inside the sandbox"
        assert result.stdout_truncated is False
        assert result.artifacts == ()

    await sandbox.runner.stop_and_remove(container_id)


@pytest.mark.asyncio
async def test_read_write_file_roundtrip(sandbox: SandboxHarness) -> None:
    """write_file creates parent directories below /workspace and read_file returns bytes."""
    async with sandbox.container() as container_id:
        payload = b"alpha\nbeta\n\x00\x01binary tail\n"
        await sandbox.runner.write_file(container_id, "notes/deep/file.bin", payload)
        assert await sandbox.runner.read_file(container_id, "notes/deep/file.bin") == payload
        assert await sandbox.runner.read_file(container_id, "/workspace/notes/deep/file.bin") == payload


@pytest.mark.asyncio
async def test_snapshot_and_restore_workspace(sandbox: SandboxHarness) -> None:
    """A workspace snapshot moves to a fresh container after a checksum verified restore."""
    tar_path = sandbox.root / "workspace.tar.gz"
    async with sandbox.container() as first_id:
        await sandbox.runner.write_file(first_id, "a.txt", b"alpha\n")
        await sandbox.runner.write_file(first_id, "notes/b.txt", b"beta\n")
        await sandbox.runner.write_file(first_id, "notes/c.md", b"# gamma\n")
        size = await sandbox.runner.snapshot_workspace(first_id, str(tar_path))

    assert size == tar_path.stat().st_size
    sidecar = Path(str(tar_path) + ".sha256")
    assert sidecar.is_file()
    checksum = sidecar.read_text(encoding="utf-8").split()[0]
    assert verify_archive(tar_path, checksum).sha256 == checksum

    async with sandbox.container() as second_id:
        await sandbox.runner.restore_workspace(second_id, str(tar_path))
        assert await sandbox.runner.read_file(second_id, "a.txt") == b"alpha\n"
        assert await sandbox.runner.read_file(second_id, "notes/b.txt") == b"beta\n"
        assert await sandbox.runner.read_file(second_id, "notes/c.md") == b"# gamma\n"


@pytest.mark.asyncio
async def test_output_cap_in_container(sandbox: SandboxHarness) -> None:
    """A command that prints 2 MB in the sandbox returns a capped payload and an artifact."""
    program = "import sys; sys.stdout.write('A' * (2 * 1024 * 1024))"
    async with sandbox.container() as container_id:
        result = await sandbox.runner.execute_command(container_id, ["python3", "-c", program])

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert len(result.stdout) == MAX_STREAM_BYTES
    assert result.stdout == "A" * MAX_STREAM_BYTES

    artifact = result.stdout_artifact
    assert artifact is not None
    artifact_path = Path(artifact)
    assert artifact_path.is_file()
    assert artifact_path.stat().st_size == 2 * 1024 * 1024 - MAX_STREAM_BYTES
    assert result.artifacts[0].total_bytes == 2 * 1024 * 1024


@pytest.mark.asyncio
async def test_read_only_root_blocks_writes_outside_workspace(sandbox: SandboxHarness) -> None:
    """The root filesystem is read only, so a write outside /workspace fails."""
    program = "open('/escape.txt', 'w').write('x')"
    async with sandbox.container() as container_id:
        outside = await sandbox.runner.execute_command(container_id, ["python3", "-c", program])
        inside = await sandbox.runner.execute_command(
            container_id,
            ["python3", "-c", "open('/workspace/ok.txt', 'w').write('x')"],
        )

    assert outside.exit_code != 0
    assert inside.exit_code == 0


@pytest.mark.asyncio
async def test_network_is_disabled(sandbox: SandboxHarness) -> None:
    """With network=none a TCP connection cannot leave the container."""
    program = (
        "import socket; socket.setdefaulttimeout(3); "
        "socket.create_connection(('1.1.1.1', 80))"
    )
    async with sandbox.container() as container_id:
        result = await sandbox.runner.execute_command(container_id, ["python3", "-c", program])

    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_stop_and_remove_is_idempotent(sandbox: SandboxHarness) -> None:
    """Removing a container twice is not an error."""
    async with sandbox.container() as container_id:
        pass
    await sandbox.runner.stop_and_remove(container_id)
