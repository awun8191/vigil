"""Unit tests for the fixed sandbox schema, the container policy, archives and output cap.

No test here needs a Docker daemon. A test that needs one lives in
``tests/integration/test_sandbox_runner.py`` and skips with a clear reason.
"""
from __future__ import annotations

import hashlib
import shutil
import stat
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config.settings import Settings
from src.integrations.repositories import SandboxRunnerProtocol
from src.integrations.sandbox.archive import (
    ArchiveError,
    ChecksumMismatch,
    checksum_sidecar_path,
    create_archive,
    record_archive,
    restore_archive,
    sha256_file,
    verify_archive,
)
from src.integrations.sandbox.contract import (
    MAX_STREAM_BYTES,
    WORKSPACE_PATH,
    SandboxPolicy,
    SandboxSpec,
    build_container_args,
    build_container_kwargs,
    container_path,
    docker_flags,
    normalize_command,
    resolve_image_allowlist,
)
from src.integrations.sandbox.sandbox import (
    SandboxError,
    SandboxRunner,
    StreamCapture,
    stream_exec,
)

ALLOWED_IMAGE = "virgil-sandbox:latest"


class SandboxTestSettings(Settings):
    """Settings with a narrow image allowlist for the tests."""

    SANDBOX_IMAGE_ALLOWLIST: str = ALLOWED_IMAGE


def make_runner(tmp_path: Path, *, docker_binary: str = "docker") -> SandboxRunner:
    """Build a runner with a temporary workspace base."""
    settings = SandboxTestSettings(APP_ENV="test", WORKSPACE_BASE_PATH=str(tmp_path / "workspaces"))
    return SandboxRunner(
        settings,
        docker_binary=docker_binary,
        workspace_base=tmp_path / "workspaces",
        artifact_dir=tmp_path / "artifacts",
    )


def write_fake_docker(tmp_path: Path, marker: Path) -> Path:
    """Write a docker binary stub that records any invocation."""
    fake = tmp_path / "fake-docker"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def flag_value(flags: list[str], flag: str) -> str:
    """Return the value after a flag token."""
    index = flags.index(flag)
    return flags[index + 1]


def flag_values(flags: list[str], flag: str) -> list[str]:
    """Return every value the flag carries."""
    return [flags[index + 1] for index, token in enumerate(flags) if token == flag]


# =============================================================================
# Payload rejection: no privileged config, no bind mount
# =============================================================================


@pytest.mark.asyncio
async def test_privileged_rejected(tmp_path: Path) -> None:
    """A HostConfig payload with Privileged is rejected before any Docker call."""
    marker = tmp_path / "docker-called"
    runner = make_runner(tmp_path, docker_binary=str(write_fake_docker(tmp_path, marker)))
    payload = {
        "image": ALLOWED_IMAGE,
        "command": ["true"],
        "HostConfig": {"Privileged": True},
    }

    with pytest.raises(ValidationError) as excinfo:
        await runner.create(payload, "run-privileged")

    assert "HostConfig" in str(excinfo.value)
    assert not marker.exists(), "the runner called Docker before it validated the payload"


@pytest.mark.asyncio
async def test_bind_rejected(tmp_path: Path) -> None:
    """A spec with a binds field is rejected before any Docker call."""
    marker = tmp_path / "docker-called"
    runner = make_runner(tmp_path, docker_binary=str(write_fake_docker(tmp_path, marker)))
    payload = {
        "image": ALLOWED_IMAGE,
        "command": ["true"],
        "binds": ["/:/host"],
    }

    with pytest.raises(ValidationError) as excinfo:
        await runner.create(payload, "run-bind")

    assert "binds" in str(excinfo.value)
    assert not marker.exists(), "the runner called Docker before it validated the payload"


@pytest.mark.asyncio
async def test_host_config_binds_rejected(tmp_path: Path) -> None:
    """The Docker style HostConfig.Binds key is rejected too."""
    runner = make_runner(tmp_path)
    payload = {
        "image": ALLOWED_IMAGE,
        "command": ["true"],
        "HostConfig": {"Binds": ["/:/host"], "PidMode": "host"},
    }

    with pytest.raises(ValidationError):
        await runner.create(payload, "run-host-config")


@pytest.mark.parametrize(
    "extra",
    [
        {"cap_add": ["SYS_ADMIN"]},
        {"capabilities": ["ALL"]},
        {"network_mode": "host"},
        {"pid_mode": "host"},
        {"userns_mode": "host"},
        {"privileged": True},
        {"volumes": {"/": {"bind": "/", "mode": "rw"}}},
    ],
)
def test_other_escape_fields_rejected(extra: dict[str, object]) -> None:
    """No field that widens privilege is part of the schema."""
    payload = {"image": ALLOWED_IMAGE, "command": ["true"], **extra}
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(payload, allowed_images=[ALLOWED_IMAGE])


def test_image_allowlist_enforced() -> None:
    """An image outside the allowlist is a ValidationError."""
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(
            {"image": "ubuntu:latest", "command": ["true"]},
            allowed_images=[ALLOWED_IMAGE],
        )


@pytest.mark.asyncio
async def test_runner_allowlist_governs_create(tmp_path: Path) -> None:
    """The runner validates again against its own allowlist, not the caller's."""
    runner = make_runner(tmp_path)
    spec = SandboxSpec.from_payload(
        {"image": "python:3.14-slim", "command": ["true"]},
        allowed_images=["python:3.14-slim"],
    )

    with pytest.raises(ValidationError):
        await runner.create(spec, "run-allowlist")


def test_allowlist_from_settings_attribute() -> None:
    """The allowlist comes from settings, not from the caller."""
    settings = SandboxTestSettings(APP_ENV="test")
    assert resolve_image_allowlist(settings) == (ALLOWED_IMAGE,)


def test_allowlist_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment can set the allowlist with the environment variable."""
    monkeypatch.setenv("SANDBOX_IMAGE_ALLOWLIST", "one:1, two:2")
    settings = Settings(APP_ENV="test")
    assert resolve_image_allowlist(settings) == ("one:1", "two:2")


def test_allowlist_falls_back_to_sandbox_image() -> None:
    """With no list configured, the single SANDBOX_IMAGE is the allowlist."""
    settings = Settings(APP_ENV="test", SANDBOX_IMAGE="only-image:latest")
    assert resolve_image_allowlist(settings) == ("only-image:latest",)


# =============================================================================
# Fixed flags
# =============================================================================


def test_fixed_flags_present() -> None:
    """Every fixed security flag of decision 3 is in the rendered container args."""
    spec = SandboxSpec.from_payload(
        {"image": ALLOWED_IMAGE, "command": ["python3", "-c", "print(1)"]},
        allowed_images=[ALLOWED_IMAGE],
    )
    args = build_container_args(spec)
    flags = list(args.flags)

    assert "--read-only" in flags
    assert "--cap-drop=ALL" in flags
    assert flag_value(flags, "--security-opt") == "no-new-privileges"
    assert "--network=none" in flags
    assert flag_value(flags, "--memory") == "1g"
    assert flag_value(flags, "--cpus") == "1.0"
    assert flag_value(flags, "--pids-limit") == "256"
    assert flag_value(flags, "--user") == "1000:1000"

    tmpfs_mounts = flag_values(flags, "--tmpfs")
    tmp_path_options = [mount for mount in tmpfs_mounts if mount.startswith("/tmp:")]
    assert len(tmp_path_options) == 1
    assert "noexec" in tmp_path_options[0].split(":")[1].split(",")

    kwargs = build_container_kwargs(spec)
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["network_mode"] == "none"
    assert kwargs["mem_limit"] == 1024 * 1024 * 1024
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["pids_limit"] == 256
    assert kwargs["user"] == "1000:1000"
    assert "noexec" in kwargs["tmpfs"]["/tmp"]

    argv = args.argv(docker="docker", name="virgil-test")
    assert argv[:3] == ["docker", "run", "--detach"]
    assert argv[argv.index(ALLOWED_IMAGE) + 1 :] == ["python3", "-c", "print(1)"]
    for required in ("--read-only", "--cap-drop=ALL", "--network=none"):
        assert required in argv


def test_policy_rejects_tmpfs_without_noexec() -> None:
    """The policy itself refuses a /tmp mount without noexec."""
    with pytest.raises(ValidationError):
        SandboxPolicy(
            memory_bytes=1024 * 1024,
            memory_swap_bytes=1024 * 1024,
            nano_cpus=1_000_000,
            tmpfs=(("/tmp", "rw,nosuid"),),
        )


def test_docker_flags_refuses_unknown_kwargs() -> None:
    """An extra kwarg cannot reach the command line."""
    with pytest.raises(ValueError):
        docker_flags({"read_only": True, "privileged": True})


def test_memory_and_cpu_ceilings_enforced() -> None:
    """A caller cannot raise the memory or CPU ceiling above the fixed limit."""
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(
            {"image": ALLOWED_IMAGE, "command": ["true"], "memory_mb": 4096},
            allowed_images=[ALLOWED_IMAGE],
        )
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(
            {"image": ALLOWED_IMAGE, "command": ["true"], "cpu_quota": 4.0},
            allowed_images=[ALLOWED_IMAGE],
        )


def test_network_literal_only_none() -> None:
    """No other network mode is expressible."""
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(
            {"image": ALLOWED_IMAGE, "command": ["true"], "network": "host"},
            allowed_images=[ALLOWED_IMAGE],
        )


# =============================================================================
# argv, never a shell string
# =============================================================================


def test_command_string_split_without_shell() -> None:
    """A string command is split into argv and never interpolated into a shell."""
    assert normalize_command("ls -la") == ("ls", "-la")
    assert normalize_command("echo hi && touch /tmp/pwned") == (
        "echo",
        "hi",
        "&&",
        "touch",
        "/tmp/pwned",
    )
    assert normalize_command(["bash", "-c", "echo hi"]) == ("bash", "-c", "echo hi")


def test_command_rejects_empty_and_nul() -> None:
    """An empty command and a NUL byte are rejected."""
    with pytest.raises(ValueError):
        normalize_command("   ")
    with pytest.raises(ValueError):
        normalize_command(["echo", "a\x00b"])


def test_spec_rejects_shell_string_command() -> None:
    """The schema takes argv tokens, not one shell string."""
    with pytest.raises(ValidationError):
        SandboxSpec.from_payload(
            {"image": ALLOWED_IMAGE, "command": "echo hi"},
            allowed_images=[ALLOWED_IMAGE],
        )


def test_empty_command_runs_image_entrypoint() -> None:
    """An empty argv is valid and leaves the image entrypoint in charge."""
    spec = SandboxSpec.from_payload({"image": ALLOWED_IMAGE}, allowed_images=[ALLOWED_IMAGE])
    assert spec.command == []
    assert build_container_args(spec).argv()[-1] == ALLOWED_IMAGE


def test_image_is_trimmed_and_command_tokens_are_not() -> None:
    """Whitespace is removed from the image reference, never from argv tokens."""
    spec = SandboxSpec.from_payload(
        {"image": f"  {ALLOWED_IMAGE}  ", "command": ["echo", " two spaces "]},
        allowed_images=[ALLOWED_IMAGE],
    )
    assert spec.image == ALLOWED_IMAGE
    assert spec.command == ["echo", " two spaces "]


# =============================================================================
# Container paths
# =============================================================================


def test_container_path_confined_to_workspace() -> None:
    """A caller path cannot leave /workspace."""
    assert container_path("a.txt") == f"{WORKSPACE_PATH}/a.txt"
    assert container_path("notes/deep.txt") == f"{WORKSPACE_PATH}/notes/deep.txt"
    assert container_path(f"{WORKSPACE_PATH}/abs.txt") == f"{WORKSPACE_PATH}/abs.txt"
    for bad in ("../etc/passwd", "/etc/passwd", "/workspace/../etc/passwd", "..", ""):
        with pytest.raises(ValueError):
            container_path(bad)


# =============================================================================
# Archive round trip
# =============================================================================


def test_archive_roundtrip(tmp_path: Path) -> None:
    """Three files survive archive, wipe and a checksum verified restore."""
    workspace = tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    (workspace / "notes" / "b.txt").write_text("beta\n", encoding="utf-8")
    (workspace / "notes" / "c.md").write_text("# gamma\nline two\n", encoding="utf-8")

    tar_path = tmp_path / "workspace.tar.gz"
    record = create_archive(workspace, tar_path)

    assert record.sha256 == sha256_file(tar_path)
    assert record.size_bytes == tar_path.stat().st_size
    assert checksum_sidecar_path(tar_path).read_text(encoding="utf-8").startswith(record.sha256)

    shutil.rmtree(workspace)
    assert not workspace.exists()

    restored = restore_archive(tar_path, workspace, expected_sha256=record.sha256)
    assert restored.sha256 == record.sha256
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (workspace / "notes" / "b.txt").read_text(encoding="utf-8") == "beta\n"
    assert (workspace / "notes" / "c.md").read_text(encoding="utf-8") == "# gamma\nline two\n"

    second = create_archive(workspace, tmp_path / "workspace-2.tar.gz")
    assert second.sha256 == record.sha256, "two archives of the same tree must match"


def test_archive_checksum_mismatch_blocks_extract(tmp_path: Path) -> None:
    """A tampered archive is refused before a single member is extracted."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    tar_path = tmp_path / "workspace.tar.gz"
    record = create_archive(workspace, tar_path)

    with open(tar_path, "ab") as handle:
        handle.write(b"tampered")

    target = tmp_path / "restored"
    with pytest.raises(ChecksumMismatch):
        restore_archive(tar_path, target, expected_sha256=record.sha256)
    assert not target.exists(), "a failed verify must not create the target"


def test_verify_archive_requires_full_checksum(tmp_path: Path) -> None:
    """A missing or truncated checksum is an error, not a bypass."""
    tar_path = tmp_path / "workspace.tar.gz"
    tar_path.write_bytes(b"")
    with pytest.raises(ArchiveError):
        verify_archive(tar_path, "")
    with pytest.raises(ArchiveError):
        verify_archive(tar_path, "abc123")


def test_record_archive_requires_existing_file(tmp_path: Path) -> None:
    """Recording a missing archive fails clearly."""
    with pytest.raises(ArchiveError):
        record_archive(tmp_path / "absent.tar.gz")


@pytest.mark.asyncio
async def test_restore_workspace_requires_checksum(tmp_path: Path) -> None:
    """The runner refuses to restore an archive without a checksum."""
    runner = make_runner(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    tar_path = tmp_path / "workspace.tar.gz"
    record = create_archive(workspace, tar_path, write_checksum=False)

    with pytest.raises(ArchiveError):
        await runner.restore_workspace("a" * 64, str(tar_path))

    assert verify_archive(tar_path, record.sha256).sha256 == record.sha256


# =============================================================================
# Output cap
# =============================================================================


@pytest.mark.asyncio
async def test_output_cap(tmp_path: Path) -> None:
    """A command that prints 2 MB returns a capped payload and an artifact reference."""
    program = "import sys; sys.stdout.write('A' * (2 * 1024 * 1024))"
    result = await stream_exec(
        [sys.executable, "-c", program],
        timeout_seconds=120,
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stderr_truncated is False
    assert len(result.stdout) == MAX_STREAM_BYTES
    assert result.stdout == "A" * MAX_STREAM_BYTES

    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.stream == "stdout"
    assert artifact.total_bytes == 2 * 1024 * 1024
    assert artifact.discarded_bytes == 2 * 1024 * 1024 - MAX_STREAM_BYTES
    assert artifact.byte_size == artifact.discarded_bytes

    artifact_path = Path(artifact.path)
    assert artifact_path.is_file()
    assert artifact_path.stat().st_size == artifact.byte_size
    assert sha256_file(artifact_path) == artifact.sha256
    assert result.stdout_artifact == str(artifact_path)
    assert result.stderr_artifact is None


@pytest.mark.asyncio
async def test_output_under_cap_has_no_artifact(tmp_path: Path) -> None:
    """Output below the cap stays in memory and has no artifact."""
    result = await stream_exec(
        [sys.executable, "-c", "print('short output')"],
        timeout_seconds=60,
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "short output"
    assert result.stdout_truncated is False
    assert result.artifacts == ()
    assert result.stdout_artifact is None


@pytest.mark.asyncio
async def test_stderr_cap_is_separate(tmp_path: Path) -> None:
    """stderr has its own 256 KB budget and its own artifact."""
    program = "import sys; sys.stderr.write('E' * (2 * 1024 * 1024))"
    result = await stream_exec(
        [sys.executable, "-c", program],
        timeout_seconds=120,
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.stdout_truncated is False
    assert result.stderr_truncated is True
    assert len(result.stderr) == MAX_STREAM_BYTES
    assert [artifact.stream for artifact in result.artifacts] == ["stderr"]
    assert result.stderr_artifact is not None


def test_stream_capture_keeps_only_the_cap(tmp_path: Path) -> None:
    """StreamCapture holds the cap in memory and sends the rest to the artifact."""
    capture = StreamCapture("stdout", cap_bytes=8, artifact_path=tmp_path / "overflow.log")
    capture.feed(b"12345")
    capture.feed(b"6789abc")
    reference = capture.close()

    assert capture.payload == b"12345678"
    assert capture.truncated is True
    assert reference is not None
    assert reference.discarded_bytes == 4
    assert reference.total_bytes == 12
    assert Path(reference.path).read_bytes() == b"9abc"
    assert reference.sha256 == hashlib.sha256(b"9abc").hexdigest()


def test_stream_capture_requires_artifact_for_overflow(tmp_path: Path) -> None:
    """Without an artifact path, overflow is an error and not a silent drop."""
    capture = StreamCapture("stdout", cap_bytes=2)
    capture.feed(b"ab")
    with pytest.raises(SandboxError):
        capture.feed(b"c")


# =============================================================================
# Protocol conformance
# =============================================================================


def test_runner_implements_protocol(tmp_path: Path) -> None:
    """The runner satisfies SandboxRunnerProtocol at runtime."""
    runner = make_runner(tmp_path)
    assert isinstance(runner, SandboxRunnerProtocol)
    for name in (
        "create_container",
        "execute_command",
        "read_file",
        "write_file",
        "snapshot_workspace",
        "restore_workspace",
        "stop_and_remove",
        "is_healthy",
    ):
        assert callable(getattr(runner, name))
