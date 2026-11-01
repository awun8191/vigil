"""Fixed request schema for the Docker sandbox runner.

The caller can express only what :class:`SandboxSpec` allows. The schema has no field for a
bind mount, a Linux capability, a host namespace, an extra container flag, or a raw Docker
payload. Unknown keys are rejected, so a caller cannot smuggle ``HostConfig`` or ``binds``
into the request.

The runner renders the security flags itself. :func:`build_container_args` is the single
source of truth for the container policy, and both the Docker CLI flags and the structured
container kwargs come from the same :class:`SandboxPolicy` instance.
"""
from __future__ import annotations

import os
import posixpath
import shlex
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from src.config.settings import Settings, get_settings
from src.integrations.repositories import SandboxExecResultDTO

# --- Hard limits. These values are part of the contract, not a tuning knob. -----------------

MAX_STREAM_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3600
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 1024
MAX_CPU_QUOTA = 1.0
NANO_CPUS_PER_CPU = 1_000_000_000
PIDS_LIMIT = 256
SANDBOX_USER = "1000:1000"
SANDBOX_UID = 1000
SANDBOX_GID = 1000
TMPFS_SIZE_MB = 64
WORKSPACE_SIZE_MB = 2048
WORKSPACE_PATH = "/workspace"
TMP_PATH = "/tmp"
ALLOWLIST_ENV_VAR = "SANDBOX_IMAGE_ALLOWLIST"

_IMAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/@:-]*$"
_CONTAINER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


# --- Image allowlist -----------------------------------------------------------------------


def _split_images(raw: object) -> tuple[str, ...]:
    """Normalize a settings value into a tuple of image references."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts: Sequence[str] = raw.split(",")
    elif isinstance(raw, Sequence):
        parts = [str(item) for item in raw]
    else:
        return ()
    return tuple(part.strip() for part in parts if part and part.strip())


def resolve_image_allowlist(settings: Settings | None = None) -> tuple[str, ...]:
    """Return the sandbox image allowlist.

    Precedence: an explicit ``SANDBOX_IMAGE_ALLOWLIST`` attribute on the settings object,
    then the ``SANDBOX_IMAGE_ALLOWLIST`` environment variable, then ``SANDBOX_IMAGE`` alone.
    The settings attribute comes first so a test or a caller can inject a narrow list.
    """
    config = settings if settings is not None else get_settings()
    images = _split_images(getattr(config, "SANDBOX_IMAGE_ALLOWLIST", None))
    if not images:
        images = _split_images(os.environ.get(ALLOWLIST_ENV_VAR, ""))
    if not images:
        images = _split_images(getattr(config, "SANDBOX_IMAGE", ""))
    if not images:
        raise ValueError("no sandbox image allowlist is configured")
    return images


# --- Spec ----------------------------------------------------------------------------------


def normalize_command(command: str | Sequence[str]) -> tuple[str, ...]:
    """Return argv tokens for a command.

    A string is split into argv with :func:`shlex.split` and never reaches a shell. A shell
    is reachable only when the caller names one as the first argv token.
    """
    if isinstance(command, str):
        if not command.strip():
            raise ValueError("command must not be empty")
        tokens = shlex.split(command, posix=True)
    else:
        tokens = list(command)
    if not tokens:
        raise ValueError("command must not be empty")
    for token in tokens:
        if not isinstance(token, str):
            raise ValueError("command must be argv tokens of type str")
        if "\x00" in token:
            raise ValueError("command tokens must not contain a NUL byte")
    if not tokens[0]:
        raise ValueError("the first command token must not be empty")
    return tuple(tokens)


def container_path(path: str) -> str:
    """Resolve a caller path to an absolute path inside the workspace.

    Relative paths land under ``/workspace``. Absolute paths are accepted only below
    ``/workspace``. Parent traversal is rejected.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    if "\x00" in path:
        raise ValueError("path must not contain a NUL byte")
    stripped = path.strip()
    if stripped.startswith("/"):
        normalized = posixpath.normpath(stripped)
        if normalized == WORKSPACE_PATH or normalized.startswith(WORKSPACE_PATH + "/"):
            return normalized
        raise ValueError(f"path must stay inside {WORKSPACE_PATH}: {path!r}")
    normalized = posixpath.normpath(stripped)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise ValueError(f"path must stay inside {WORKSPACE_PATH}: {path!r}")
    return f"{WORKSPACE_PATH}/{normalized}"


class SandboxSpec(BaseModel):
    """The only request the sandbox accepts.

    ``extra="forbid"`` is the boundary that rejects a privileged payload or a bind mount.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=_IMAGE_PATTERN,
        description="Container image reference. It must match the settings allowlist.",
    )
    command: list[str] = Field(
        default_factory=list,
        max_length=256,
        description="argv tokens. An empty list runs the image entrypoint.",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_TIMEOUT_SECONDS,
        description="Hard limit for the container or command lifetime.",
    )
    memory_mb: int = Field(
        default=MAX_MEMORY_MB,
        ge=MIN_MEMORY_MB,
        le=MAX_MEMORY_MB,
        description="Memory ceiling in MB. The runner never grants more than 1024 MB.",
    )
    cpu_quota: float = Field(
        default=MAX_CPU_QUOTA,
        gt=0.0,
        le=MAX_CPU_QUOTA,
        description="CPU ceiling. The runner never grants more than 1.0 CPU.",
    )
    network: Literal["none"] = Field(
        default="none",
        description="Network mode. Only 'none' is accepted.",
    )

    @field_validator("image", mode="before")
    @classmethod
    def _strip_image(cls, value: object) -> object:
        """Trim an image reference without touching command tokens."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("command")
    @classmethod
    def _check_command(cls, value: list[str]) -> list[str]:
        """Reject NUL bytes and empty first tokens."""
        for token in value:
            if not isinstance(token, str):
                raise ValueError("command must contain strings only")
            if "\x00" in token:
                raise ValueError("command tokens must not contain a NUL byte")
        if value and not value[0]:
            raise ValueError("the first command token must not be empty")
        return value

    @model_validator(mode="after")
    def _check_image_allowlist(self, info: ValidationInfo) -> "SandboxSpec":
        """Reject an image outside the settings allowlist."""
        allowed: object = None
        if info.context:
            allowed = info.context.get("allowed_images")
        images = _split_images(allowed) if allowed is not None else resolve_image_allowlist()
        if self.image not in images:
            raise ValueError(f"image {self.image!r} is not in the sandbox image allowlist")
        return self

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        allowed_images: Sequence[str] | None = None,
    ) -> "SandboxSpec":
        """Validate a raw payload. Raises ``pydantic.ValidationError`` on any violation."""
        if not isinstance(payload, Mapping):
            raise TypeError("sandbox payload must be a mapping")
        context = None
        if allowed_images is not None:
            context = {"allowed_images": tuple(allowed_images)}
        return cls.model_validate(dict(payload), context=context)


# --- Fixed container policy ----------------------------------------------------------------


def _format_bytes(value: int) -> str:
    """Render a byte count as a Docker size string (1 GiB becomes '1g')."""
    for unit, size in (("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10)):
        if value and value % size == 0:
            return f"{value // size}{unit}"
    return str(value)


def _format_cpus(nano_cpus: int) -> str:
    """Render nano CPUs as a decimal string ('1.0', '0.5', '1.25')."""
    text = f"{nano_cpus / NANO_CPUS_PER_CPU:.2f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


class SandboxPolicy(BaseModel):
    """The security policy of every sandbox container. It is not caller configurable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    read_only_root: bool = True
    cap_drop: tuple[str, ...] = ("ALL",)
    security_opts: tuple[str, ...] = ("no-new-privileges",)
    network_mode: Literal["none"] = "none"
    memory_bytes: int = Field(..., gt=0, le=MAX_MEMORY_MB * 1024 * 1024)
    memory_swap_bytes: int = Field(..., gt=0, le=MAX_MEMORY_MB * 1024 * 1024)
    nano_cpus: int = Field(..., gt=0, le=NANO_CPUS_PER_CPU)
    pids_limit: int = Field(default=PIDS_LIMIT, ge=1, le=4096)
    user: str = SANDBOX_USER
    tmpfs: tuple[tuple[str, str], ...] = Field(...)

    @model_validator(mode="after")
    def _check_tmpfs(self) -> "SandboxPolicy":
        """Require a tmpfs at /tmp with noexec, and refuse a tmpfs at the root."""
        mounts = dict(self.tmpfs)
        if TMP_PATH not in mounts:
            raise ValueError(f"the policy must mount a tmpfs at {TMP_PATH}")
        if "noexec" not in mounts[TMP_PATH].split(","):
            raise ValueError(f"the {TMP_PATH} tmpfs must set noexec")
        if "/" in mounts:
            raise ValueError("the policy must not mount a tmpfs at the container root")
        return self

    @classmethod
    def from_spec(cls, spec: SandboxSpec) -> "SandboxPolicy":
        """Derive the fixed policy for a validated spec."""
        memory_bytes = spec.memory_mb * 1024 * 1024
        return cls(
            memory_bytes=memory_bytes,
            memory_swap_bytes=memory_bytes,
            nano_cpus=int(round(spec.cpu_quota * NANO_CPUS_PER_CPU)),
            tmpfs=(
                (TMP_PATH, f"rw,noexec,nosuid,size={TMPFS_SIZE_MB}m"),
                (
                    WORKSPACE_PATH,
                    f"rw,nosuid,size={WORKSPACE_SIZE_MB}m,uid={SANDBOX_UID},gid={SANDBOX_GID},mode=0770",
                ),
            ),
        )

    def as_kwargs(self) -> dict[str, Any]:
        """Return the container creation kwargs in Docker SDK form."""
        return {
            "read_only": self.read_only_root,
            "cap_drop": list(self.cap_drop),
            "security_opt": list(self.security_opts),
            "network_mode": self.network_mode,
            "mem_limit": self.memory_bytes,
            "memswap_limit": self.memory_swap_bytes,
            "nano_cpus": self.nano_cpus,
            "pids_limit": self.pids_limit,
            "user": self.user,
            "tmpfs": dict(self.tmpfs),
        }


def docker_flags(kwargs: Mapping[str, Any]) -> tuple[str, ...]:
    """Render container kwargs as Docker CLI flag tokens.

    Only the keys that the policy produces are accepted. Unknown keys raise ``ValueError``
    so no extra flag can enter the command line.
    """
    handled = {
        "read_only",
        "cap_drop",
        "security_opt",
        "network_mode",
        "mem_limit",
        "memswap_limit",
        "nano_cpus",
        "pids_limit",
        "user",
        "tmpfs",
    }
    extra = set(kwargs) - handled
    if extra:
        raise ValueError(f"unsupported container kwargs: {sorted(extra)}")

    flags: list[str] = []
    if kwargs.get("read_only"):
        flags.append("--read-only")
    for capability in kwargs.get("cap_drop", ()):
        flags.append(f"--cap-drop={capability}")
    for option in kwargs.get("security_opt", ()):
        flags.extend(["--security-opt", str(option)])
    if kwargs.get("network_mode"):
        flags.append(f"--network={kwargs['network_mode']}")
    if kwargs.get("mem_limit"):
        flags.extend(["--memory", _format_bytes(int(kwargs["mem_limit"]))])
    if kwargs.get("memswap_limit"):
        flags.extend(["--memory-swap", _format_bytes(int(kwargs["memswap_limit"]))])
    if kwargs.get("nano_cpus"):
        flags.extend(["--cpus", _format_cpus(int(kwargs["nano_cpus"]))])
    if kwargs.get("pids_limit"):
        flags.extend(["--pids-limit", str(int(kwargs["pids_limit"]))])
    if kwargs.get("user"):
        flags.extend(["--user", str(kwargs["user"])])
    for target, options in dict(kwargs.get("tmpfs", {})).items():
        flags.extend(["--tmpfs", f"{target}:{options}"])
    return tuple(flags)


class ContainerArgs(BaseModel):
    """Everything the runner needs to start one container."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str
    command: tuple[str, ...]
    flags: tuple[str, ...]
    kwargs: dict[str, Any]
    timeout_seconds: int

    def argv(
        self,
        docker: str = "docker",
        *,
        name: str | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Build the ``docker run`` argv. No shell is involved."""
        argv = [docker, "run", "--detach"]
        if name:
            argv.extend(["--name", name])
        for key, value in (labels or {}).items():
            argv.extend(["--label", f"{key}={value}"])
        argv.extend(self.flags)
        argv.append(self.image)
        argv.extend(self.command)
        return argv


def build_container_kwargs(spec: SandboxSpec) -> dict[str, Any]:
    """Build the structured container kwargs for a validated spec."""
    return SandboxPolicy.from_spec(spec).as_kwargs()


def build_container_args(spec: SandboxSpec) -> ContainerArgs:
    """Build the container args for a validated spec from the fixed policy."""
    kwargs = build_container_kwargs(spec)
    return ContainerArgs(
        image=spec.image,
        command=tuple(spec.command),
        flags=docker_flags(kwargs),
        kwargs=kwargs,
        timeout_seconds=spec.timeout_seconds,
    )


# --- Result types --------------------------------------------------------------------------


class SandboxArtifactRef(BaseModel):
    """A reference to overflow output that the runner spilled to disk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream: Literal["stdout", "stderr"]
    path: str = Field(..., description="Absolute path of the artifact on the host")
    byte_size: int = Field(..., ge=0, description="Bytes in the artifact")
    total_bytes: int = Field(..., ge=0, description="Bytes the stream produced in total")
    discarded_bytes: int = Field(
        ...,
        ge=0,
        description="Bytes dropped from the in-memory payload",
    )
    sha256: str = Field(..., description="SHA-256 of the artifact content")


class SandboxExecResult(SandboxExecResultDTO):
    """Exec output with the per stream cap recorded and artifacts referenced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stdout_truncated: bool = False
    stderr_truncated: bool = False
    artifacts: tuple[SandboxArtifactRef, ...] = ()

    @property
    def stdout_artifact(self) -> str | None:
        """Absolute path of the stdout overflow artifact, if the cap was hit."""
        return self._artifact_path("stdout")

    @property
    def stderr_artifact(self) -> str | None:
        """Absolute path of the stderr overflow artifact, if the cap was hit."""
        return self._artifact_path("stderr")

    def _artifact_path(self, stream: str) -> str | None:
        for artifact in self.artifacts:
            if artifact.stream == stream:
                return artifact.path
        return None
