"""Docker sandbox runner with a fixed request schema.

Only this integration layer talks to the Docker daemon. The caller passes a
:class:`~src.integrations.sandbox.contract.SandboxSpec`. The runner renders every security
flag itself from a fixed policy, so a request cannot express a bind mount, a Linux
capability, a host namespace, or a raw Docker payload.

Output of a command is capped at 256 KB per stream. Bytes above the cap go to an artifact
file on disk, never into memory.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from src.config.settings import Settings, get_settings
from src.integrations.repositories import SandboxContainerDTO
from src.integrations.sandbox.archive import (
    ArchiveError,
    record_archive,
    read_checksum_sidecar,
    verify_archive,
    write_checksum_sidecar,
)
from src.integrations.sandbox.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_STREAM_BYTES,
    MAX_TIMEOUT_SECONDS,
    WORKSPACE_PATH,
    ContainerArgs,
    SandboxArtifactRef,
    SandboxExecResult,
    SandboxSpec,
    build_container_args,
    container_path,
    normalize_command,
    resolve_image_allowlist,
)

CHUNK_BYTES = 64 * 1024
ARCHIVE_TIMEOUT_SECONDS = 300
CREATE_TIMEOUT_SECONDS = 120
RAISE_TIMEOUT_SECONDS = 60

_CONTAINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_NAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DAEMON_ERROR_MARKERS = (
    "Error response from daemon:",
    "No such container",
    "is not running",
    "Cannot connect to the Docker daemon",
)


class SandboxError(RuntimeError):
    """Base error for sandbox transport and daemon failures."""


class SandboxUnavailable(SandboxError):
    """The Docker binary or daemon is not reachable."""


class SandboxTimeout(SandboxError):
    """A command or container operation exceeded its time limit."""


class SandboxExecFailed(SandboxError):
    """The Docker daemon refused an operation."""


# =============================================================================
# Stream capture with a hard memory cap
# =============================================================================


class StreamCapture:
    """Collect one output stream, keep at most ``cap_bytes`` in memory, spill the rest.

    `payload` always holds the first ``cap_bytes`` of the stream. Bytes above the cap are
    appended to ``artifact_path`` and hashed as they pass, so the runner never holds the full
    output in memory.
    """

    def __init__(
        self,
        stream: Literal["stdout", "stderr"],
        *,
        cap_bytes: int = MAX_STREAM_BYTES,
        artifact_path: Path | None = None,
    ) -> None:
        if stream not in ("stdout", "stderr"):
            raise ValueError("stream must be 'stdout' or 'stderr'")
        if cap_bytes <= 0:
            raise ValueError("cap_bytes must be positive")
        self.stream: Literal["stdout", "stderr"] = stream
        self.cap_bytes = cap_bytes
        self.artifact_path = artifact_path
        self._payload = bytearray()
        self._total_bytes = 0
        self._discarded_bytes = 0
        self._digest = hashlib.sha256()
        self._handle = None
        self._ref: SandboxArtifactRef | None = None

    @property
    def truncated(self) -> bool:
        """True when the stream exceeded the in-memory cap."""
        return self._discarded_bytes > 0

    @property
    def total_bytes(self) -> int:
        """Total bytes seen on the stream."""
        return self._total_bytes

    @property
    def payload(self) -> bytes:
        """The capped payload held in memory."""
        return bytes(self._payload)

    def feed(self, chunk: bytes) -> None:
        """Record one chunk of stream output."""
        if not chunk:
            return
        self._total_bytes += len(chunk)
        room = self.cap_bytes - len(self._payload)
        if room > 0:
            self._payload.extend(chunk[:room])
            chunk = chunk[room:]
        if not chunk:
            return
        if self.artifact_path is None:
            raise SandboxError(f"{self.stream} exceeded the output cap and no artifact path is set")
        if self._handle is None:
            self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.artifact_path, "wb")
        self._handle.write(chunk)
        self._digest.update(chunk)
        self._discarded_bytes += len(chunk)

    def close(self) -> SandboxArtifactRef | None:
        """Flush the artifact and return its reference, or None when nothing spilled."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        if self._discarded_bytes == 0 or self.artifact_path is None:
            return None
        if self._ref is None:
            self._ref = SandboxArtifactRef(
                stream=self.stream,
                path=str(self.artifact_path),
                byte_size=self._discarded_bytes,
                total_bytes=self._total_bytes,
                discarded_bytes=self._discarded_bytes,
                sha256=self._digest.hexdigest(),
            )
        return self._ref

    @classmethod
    async def capture(
        cls,
        reader: asyncio.StreamReader,
        stream: Literal["stdout", "stderr"],
        *,
        cap_bytes: int = MAX_STREAM_BYTES,
        artifact_path: Path | None = None,
    ) -> "StreamCapture":
        """Drain a reader into a capture in fixed size chunks."""
        capture = cls(stream, cap_bytes=cap_bytes, artifact_path=artifact_path)
        try:
            while True:
                chunk = await reader.read(CHUNK_BYTES)
                if not chunk:
                    break
                capture.feed(chunk)
        finally:
            capture.close()
        return capture


def _decode(payload: bytes) -> str:
    """Decode command output without raising on invalid UTF-8."""
    return payload.decode("utf-8", errors="replace")


def _artifact_paths(artifact_dir: Path) -> tuple[Path, Path]:
    """Return distinct artifact paths for the stdout and stderr overflow."""
    token = uuid.uuid4().hex
    return (
        artifact_dir / f"exec-{token}-stdout.log",
        artifact_dir / f"exec-{token}-stderr.log",
    )


async def stream_exec(
    argv: Sequence[str],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    cap_bytes: int = MAX_STREAM_BYTES,
    artifact_dir: str | Path | None = None,
    stdin: bytes | None = None,
) -> SandboxExecResult:
    """Run argv and return capped output.

    The same executor serves ``docker exec`` and local argv. No shell is involved. A stream
    above ``cap_bytes`` keeps its first ``cap_bytes`` in memory and writes the rest to an
    artifact under ``artifact_dir``.
    """
    if not argv:
        raise ValueError("argv must not be empty")
    if artifact_dir is None:
        raise ValueError("artifact_dir is required to spill output above the cap")
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = _artifact_paths(directory)

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc
    except OSError as exc:
        raise SandboxUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc

    assert process.stdout is not None
    assert process.stderr is not None

    async def feed_stdin() -> None:
        if stdin is None or process.stdin is None:
            return
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()

    stdin_task = asyncio.create_task(feed_stdin())
    stdout_task = asyncio.create_task(
        StreamCapture.capture(process.stdout, "stdout", cap_bytes=cap_bytes, artifact_path=stdout_path)
    )
    stderr_task = asyncio.create_task(
        StreamCapture.capture(process.stderr, "stderr", cap_bytes=cap_bytes, artifact_path=stderr_path)
    )

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        for task in (stdin_task, stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdin_task, stdout_task, stderr_task, return_exceptions=True)
        raise SandboxTimeout(f"command exceeded {timeout_seconds} seconds: {argv[0]}")
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise

    await asyncio.gather(stdin_task, stdout_task, stderr_task, return_exceptions=True)
    stdout_capture = stdout_task.result()
    stderr_capture = stderr_task.result()

    artifacts = tuple(
        artifact for artifact in (stdout_capture.close(), stderr_capture.close()) if artifact is not None
    )
    return SandboxExecResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=_decode(stdout_capture.payload),
        stderr=_decode(stderr_capture.payload),
        duration_ms=(time.monotonic() - started) * 1000.0,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        artifacts=artifacts,
    )


async def _run(
    argv: Sequence[str],
    *,
    timeout_seconds: float = RAISE_TIMEOUT_SECONDS,
    stdin: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    """Run argv and return the exit code with buffered output."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc
    except OSError as exc:
        raise SandboxUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc
    try:
        out, err = await asyncio.wait_for(process.communicate(input=stdin), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise SandboxTimeout(f"command exceeded {timeout_seconds} seconds: {argv[0]}")
    return process.returncode or 0, out, err


async def _stream_to_file(
    argv: Sequence[str],
    target: Path,
    *,
    timeout_seconds: float = ARCHIVE_TIMEOUT_SECONDS,
) -> None:
    """Run argv and write its stdout to a file without buffering it in memory."""
    process: asyncio.subprocess.Process | None = None
    err = b""
    try:
        with open(target, "wb") as handle:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, err = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise SandboxTimeout(f"archive exceeded {timeout_seconds} seconds")
    except FileNotFoundError as exc:
        raise SandboxUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc
    if process is not None and process.returncode:
        raise SandboxExecFailed(
            f"{argv[0]} failed with exit code {process.returncode}: {_decode(err).strip()}"
        )


def _detail(stderr: bytes | str) -> str:
    """Decode a Docker CLI error stream."""
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace").strip()
    return stderr.strip()


def _raise_if_daemon_error(code: int, stderr: bytes | str, context: str) -> None:
    """Raise only when the Docker client reports a daemon level failure.

    A command that exits non-zero inside the container is a normal result, not an error.
    ``docker exec`` propagates that exit code, so the marker check keeps the two apart.
    """
    if code == 0:
        return
    message = _detail(stderr)
    if any(marker in message for marker in _DAEMON_ERROR_MARKERS):
        raise SandboxExecFailed(f"{context}: {message}")


def _require_success(code: int, stderr: bytes | str, context: str) -> None:
    """Raise on any non-zero exit. Used where success is the only valid outcome."""
    if code == 0:
        return
    message = _detail(stderr)
    raise SandboxExecFailed(f"{context} failed with exit code {code}: {message}")


def _check_container_id(container_id: str) -> str:
    """Validate a container identifier before it reaches argv."""
    if not isinstance(container_id, str) or not _CONTAINER_ID_RE.match(container_id):
        raise ValueError(f"invalid container id: {container_id!r}")
    return container_id


def _container_name(run_id: str) -> str:
    """Build a Docker safe container name from a run id."""
    token = _NAME_UNSAFE_RE.sub("-", str(run_id)).strip("-.")[:40] or "run"
    return f"virgil-{token}-{uuid.uuid4().hex[:8]}"


# =============================================================================
# Runner
# =============================================================================


class SandboxRunner:
    """Docker CLI implementation of ``SandboxRunnerProtocol``.

    The class never imports the Docker SDK. It renders a fixed flag set and calls the Docker
    CLI, which keeps the container policy in one place.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        docker_binary: str | None = None,
        workspace_base: str | Path | None = None,
        artifact_dir: str | Path | None = None,
    ) -> None:
        self.settings = settings if settings is not None else get_settings()
        self.docker = docker_binary or os.environ.get("DOCKER_BINARY") or "docker"
        base = Path(workspace_base if workspace_base is not None else self.settings.WORKSPACE_BASE_PATH)
        self.workspace_base = base
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else base / "artifacts"
        self.allowed_images = resolve_image_allowlist(self.settings)

    # --- spec handling ------------------------------------------------------------------

    def validate_spec(self, spec: SandboxSpec | Mapping[str, Any]) -> SandboxSpec:
        """Validate a spec or a raw payload against the runner allowlist.

        A payload with an extra key such as ``HostConfig`` or ``binds`` raises
        ``pydantic.ValidationError`` here, before any Docker call.
        """
        payload = spec.model_dump() if isinstance(spec, SandboxSpec) else spec
        return SandboxSpec.from_payload(payload, allowed_images=self.allowed_images)

    def container_args(self, spec: SandboxSpec) -> ContainerArgs:
        """Build the container args for a validated spec."""
        return build_container_args(self.validate_spec(spec))

    # --- lifecycle ----------------------------------------------------------------------

    async def create(
        self,
        spec: SandboxSpec | Mapping[str, Any],
        run_id: str,
    ) -> SandboxContainerDTO:
        """Create a detached container from a validated spec."""
        validated = self.validate_spec(spec)
        args = build_container_args(validated)
        name = _container_name(run_id)
        code, out, err = await _run(
            args.argv(
                self.docker,
                name=name,
                labels={
                    "virgil.run_id": _NAME_UNSAFE_RE.sub("-", str(run_id))[:64] or "run",
                    "virgil.timeout_seconds": str(validated.timeout_seconds),
                },
            ),
            timeout_seconds=CREATE_TIMEOUT_SECONDS,
        )
        _require_success(code, err, f"docker run for run {run_id}")
        container_id = _decode(out).strip().splitlines()[-1].strip() if out.strip() else ""
        if not container_id:
            raise SandboxError(f"docker run returned no container id for run {run_id}")
        return SandboxContainerDTO(
            container_id=container_id,
            workspace_path=WORKSPACE_PATH,
            created_at=time.time(),
        )

    async def create_container(
        self,
        run_id: str,
        timeout_seconds: int = 900,
        *,
        spec: SandboxSpec | Mapping[str, Any] | None = None,
    ) -> SandboxContainerDTO:
        """Protocol entry point. Uses the image allowlist head when no spec is given."""
        if spec is None:
            spec = SandboxSpec(
                image=self.allowed_images[0],
                command=[],
                timeout_seconds=min(max(int(timeout_seconds), 1), MAX_TIMEOUT_SECONDS),
            )
        return await self.create(spec, run_id)

    async def execute_command(
        self,
        container_id: str,
        command: str | Sequence[str],
        timeout_seconds: int = 60,
    ) -> SandboxExecResult:
        """Run argv inside a container with a capped, artifact backed output."""
        cid = _check_container_id(container_id)
        tokens = normalize_command(command)
        argv = [self.docker, "exec", cid, *tokens]
        result = await stream_exec(
            argv,
            timeout_seconds=timeout_seconds,
            cap_bytes=MAX_STREAM_BYTES,
            artifact_dir=self.artifact_dir,
        )
        _raise_if_daemon_error(result.exit_code, result.stderr, f"docker exec in {cid}")
        return result

    async def read_file(self, container_id: str, path: str) -> bytes:
        """Read a file below /workspace as raw bytes."""
        cid = _check_container_id(container_id)
        target = container_path(path)
        code, out, err = await _run([self.docker, "exec", cid, "cat", "--", target])
        _raise_if_daemon_error(code, err, f"read {target} from {cid}")
        if code != 0:
            message = _detail(err)
            if "No such file" in message:
                raise FileNotFoundError(message)
            _require_success(code, err, f"read {target} from {cid}")
        return out

    async def write_file(self, container_id: str, path: str, content: bytes) -> None:
        """Write raw bytes to a file below /workspace."""
        cid = _check_container_id(container_id)
        target = container_path(path)
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError("content must be bytes")
        parent = posixpath.dirname(target)
        if parent and parent != WORKSPACE_PATH:
            code, _, err = await _run([self.docker, "exec", cid, "mkdir", "-p", "--", parent])
            _require_success(code, err, f"create {parent} in {cid}")
        code, _, err = await _run(
            [self.docker, "exec", "-i", cid, "dd", f"of={target}", "status=none"],
            stdin=bytes(content),
        )
        _require_success(code, err, f"write {target} to {cid}")

    async def snapshot_workspace(self, container_id: str, target_tar_path: str) -> int:
        """Archive /workspace to a host tarball and write its checksum sidecar."""
        cid = _check_container_id(container_id)
        target = Path(target_tar_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await _stream_to_file(
            [self.docker, "exec", cid, "tar", "-C", WORKSPACE_PATH, "-cf", "-", "."],
            target,
        )
        record = record_archive(target)
        write_checksum_sidecar(record)
        return record.size_bytes

    async def restore_workspace(
        self,
        container_id: str,
        source_tar_path: str,
        *,
        expected_sha256: str | None = None,
    ) -> None:
        """Verify the archive checksum, then extract it into /workspace."""
        cid = _check_container_id(container_id)
        source = Path(source_tar_path)
        checksum = expected_sha256 or read_checksum_sidecar(source)
        if not checksum:
            raise ArchiveError(f"a checksum is required to restore {source}")
        verify_archive(source, checksum)
        with open(source, "rb") as handle:
            process = await asyncio.create_subprocess_exec(
                self.docker,
                "exec",
                "-i",
                cid,
                "tar",
                "-C",
                WORKSPACE_PATH,
                "-xf",
                "-",
                "--no-overwrite-dir",
                "--no-same-owner",
                stdin=handle,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, err = await asyncio.wait_for(process.communicate(), timeout=ARCHIVE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise SandboxTimeout(f"restore exceeded {ARCHIVE_TIMEOUT_SECONDS} seconds")
        _require_success(process.returncode or 0, err, f"restore workspace in {cid}")

    async def stop_and_remove(self, container_id: str) -> None:
        """Force remove a container. A missing container is not an error."""
        cid = _check_container_id(container_id)
        code, _, err = await _run([self.docker, "rm", "--force", cid])
        if code != 0 and "No such container" not in _decode(err):
            _require_success(code, err, f"remove container {cid}")

    async def is_healthy(self) -> bool:
        """Return True when the Docker daemon answers."""
        if not self.docker:
            return False
        try:
            code, out, _ = await _run([self.docker, "info", "--format", "{{.ServerVersion}}"], timeout_seconds=15)
        except (SandboxError, OSError):
            return False
        return code == 0 and bool(out.strip())

    async def image_available(self, image: str) -> bool:
        """Return True when an image is present in the local daemon store."""
        code, out, _ = await _run([self.docker, "image", "inspect", "--format", "{{.Id}}", image])
        return code == 0 and bool(out.strip())


def docker_binary_available(binary: str = "docker") -> bool:
    """Return True when the Docker CLI exists on PATH."""
    return shutil.which(binary) is not None


__all__ = [
    "SandboxError",
    "SandboxExecFailed",
    "SandboxRunner",
    "SandboxTimeout",
    "SandboxUnavailable",
    "StreamCapture",
    "docker_binary_available",
    "stream_exec",
]
