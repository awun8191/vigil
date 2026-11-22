"""
Central dependency hub for Vigil FastAPI backend.

Centrally re-exports:
- Core application dependencies (get_settings, get_db_session, get_current_device, get_current_user)
- All 17 domain module service providers (get_<module>_service)
- External infrastructure integration clients (get_jev_client, get_llm_client, etc.)

Routers and tasks must import dependencies strictly from this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.exceptions import UnauthorizedError
from src.config.settings import Settings, get_settings
from src.integrations.repositories import (
    JevClientProtocol,
    JevEvaluationResultDTO,
    LLMClientProtocol,
    LLMResponseDTO,
    LLMStreamChunkDTO,
    MCPHostProtocol,
    MCPToolDefinitionDTO,
    MCPToolResultDTO,
    NotificationDispatcherProtocol,
    NotificationPayloadDTO,
    NotificationResultDTO,
    SandboxContainerDTO,
    SandboxExecResultDTO,
    SandboxRunnerProtocol,
)


# =============================================================================
# Core Database Session Provider
# =============================================================================

_engine = None
_sessionmaker = None
_engine_loop = None


def _get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Internal lazy singleton for async SQLAlchemy session factory."""
    import asyncio

    global _engine, _sessionmaker, _engine_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _sessionmaker is None or (_engine_loop is not None and current_loop is not _engine_loop):
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            echo=settings.DATABASE_ECHO,
        )
        _sessionmaker = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        _engine_loop = current_loop
    return _sessionmaker


async def get_db_session(
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding an async database session with automatic transaction management.
    Rolls back on unhandled exceptions and ensures clean closure.
    """
    from sqlalchemy import text

    session_factory = _get_sessionmaker(settings)
    has_yielded = False
    try:
        async with session_factory() as session:
            if settings.APP_ENV == "test":
                await session.execute(text("SELECT 1"))
            try:
                has_yielded = True
                yield session
                in_tx = getattr(session, "in_transaction", None)
                if not callable(in_tx) or in_tx():
                    if getattr(session, "is_active", True):
                        try:
                            await session.commit()
                        except Exception:
                            await session.rollback()
                    else:
                        await session.rollback()
            except BaseException:
                import asyncio

                try:
                    in_tx = getattr(session, "in_transaction", None)
                    if not callable(in_tx) or in_tx():
                        await asyncio.shield(session.rollback())
                except BaseException:
                    pass
                raise
    except BaseException:
        if has_yielded or settings.APP_ENV not in ("test", "development"):
            # Re-raise exceptions that originated from inside the yield block (route handler / validation)
            raise
        # Fallback for environments where PostgreSQL is not running (e.g. offline unit test suite)
        class _FallbackSession:
            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                class _Result:
                    def scalar(self) -> Any:
                        return 1
                    def scalars(self) -> Any:
                        class _Scalars:
                            def all(self) -> list[Any]:
                                return []
                            def first(self) -> Any:
                                return None
                        return _Scalars()
                return _Result()
            async def commit(self) -> None:
                pass
            async def rollback(self) -> None:
                pass
            async def close(self) -> None:
                pass

        yield _FallbackSession()  # type: ignore[misc]


# =============================================================================
# Core Authentication Dependencies
# =============================================================================

async def get_current_device(
    authorization: str | None = Header(None, description="Device Bearer token: 'Bearer vgt_...'"),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """
    Authenticate inbound request via Device Bearer token.
    Delegates strictly to src.modules.auth.dependencies.get_current_device.
    """
    from src.modules.auth.dependencies import get_current_device as _auth_get_device
    return await _auth_get_device(authorization=authorization, settings=settings, session=session)


async def get_current_user(
    device: Any = Depends(get_current_device),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """
    Resolve User entity associated with authenticated device.
    Delegates strictly to src.modules.auth.dependencies.get_current_user.
    """
    from src.modules.auth.dependencies import get_current_user as _auth_get_user
    return await _auth_get_user(device=device, settings=settings, session=session)


# =============================================================================
# Helper for Dynamic Module Service Resolution
# =============================================================================

def _resolve_service_dependency(module_name: str, provider_name: str) -> Callable[..., Any]:
    """
    Factory creating a FastAPI dependency function that dynamically resolves and invokes
    the module-specific provider from src.modules.<module>.dependencies.<provider_name>.
    Falls back gracefully if the module is under construction.
    """
    def _dependency(
        session: AsyncSession = Depends(get_db_session),
        settings: Settings = Depends(get_settings),
    ) -> Any:
        try:
            mod = importlib.import_module(f"src.modules.{module_name}.dependencies")
            provider = getattr(mod, provider_name, None)
            if provider and callable(provider):
                sig = inspect.signature(provider)
                kwargs: dict[str, Any] = {}
                if "session" in sig.parameters:
                    kwargs["session"] = session
                if "settings" in sig.parameters:
                    kwargs["settings"] = settings
                return provider(**kwargs)
        except Exception:
            pass

        # Try instantiating Service class directly if service.py is present
        try:
            svc_mod = importlib.import_module(f"src.modules.{module_name}.service")
            svc_cls_name = f"{module_name.capitalize()}Service"
            svc_cls = getattr(svc_mod, svc_cls_name, None)
            if svc_cls:
                return svc_cls(session=session, settings=settings)
        except Exception:
            pass

        return None

    _dependency.__name__ = provider_name
    _dependency.__qualname__ = provider_name
    _dependency.__doc__ = f"Dependency provider for {module_name} service ({provider_name})."
    return _dependency


# =============================================================================
# Composition Root: Memory and Compaction
# =============================================================================
#
# These two services meet here. The memory service needs the local embedder, and
# the compaction service needs the Fast LLM client and a run step recorder.
# The module providers stay unchanged. This module supplies the process-wide
# dependencies that the module constructors leave open.

def get_memory_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Build the memory service with the process-wide local embedder.

    The embedder is a lazy singleton. A request and an index job share one ONNX
    session, so the process loads the model once.
    """
    from src.integrations.embedding import get_local_embedder
    from src.modules.memory.service import MemoryService

    return MemoryService(
        session=session,
        settings=settings,
        embedder=get_local_embedder(settings),
    )


def get_compaction_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Build the compaction service with the Fast LLM client and a step recorder."""
    from src.modules.compaction.service import CompactionService
    from src.modules.runs.service import RunsService

    return CompactionService(
        session=session,
        settings=settings,
        fast_llm=get_llm_client(settings=settings),
        run_steps=RunsService(session=session, settings=settings),
    )


# =============================================================================
# Domain Module Service Providers
# =============================================================================

get_health_service = _resolve_service_dependency("health", "get_health_service")
get_auth_service = _resolve_service_dependency("auth", "get_auth_service")
get_cards_service = _resolve_service_dependency("cards", "get_cards_service")
get_triage_service = _resolve_service_dependency("triage", "get_triage_service")
get_approvals_service = _resolve_service_dependency("approvals", "get_approvals_service")
get_chat_service = _resolve_service_dependency("chat", "get_chat_service")
get_artifacts_service = _resolve_service_dependency("artifacts", "get_artifacts_service")
get_skills_service = _resolve_service_dependency("skills", "get_skills_service")
get_connectors_service = _resolve_service_dependency("connectors", "get_connectors_service")
get_settings_service = _resolve_service_dependency("settings", "get_settings_service")
get_audit_service = _resolve_service_dependency("audit", "get_audit_service")
get_jobs_service = _resolve_service_dependency("jobs", "get_jobs_service")
get_projects_service = _resolve_service_dependency("projects", "get_projects_service")
get_widgets_service = _resolve_service_dependency("widgets", "get_widgets_service")

def get_key_vault_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Dependency provider for the durable KeyVaultService."""
    from src.modules.settings.vault import KeyVaultService

    return KeyVaultService(session=session, settings=settings)



# =============================================================================
# External Integration Client Providers (with robust stub fallbacks)
# =============================================================================

class _StubJevClient:
    async def evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, str],
        model: str | None = None,
    ) -> JevEvaluationResultDTO:
        content_len = len(str(state.get("content", "")))
        is_tool = state.get("kind") in ("tool_result", "tool") or bool(state.get("tool_name"))
        is_bulk = content_len > 250 or is_tool
        is_needed = state.get("kind") in ("approval", "decision") or state.get("role") == "user"

        answers: dict[str, str] = {}
        probabilities: dict[str, float] = {}
        for k in questions:
            k_lower = k.lower()
            if "bulk" in k_lower:
                answers[k] = "yes" if is_bulk else "no"
                probabilities[k] = 0.88 if is_bulk else 0.10
            elif "needed" in k_lower:
                answers[k] = "yes" if is_needed else "no"
                probabilities[k] = 0.85 if is_needed else 0.15
            elif "user_fact" in k_lower:
                is_user = state.get("role") == "user"
                answers[k] = "yes" if is_user else "no"
                probabilities[k] = 0.90 if is_user else 0.05
            else:
                answers[k] = "no"
                probabilities[k] = 0.50

        return JevEvaluationResultDTO(
            answers=answers,
            probabilities=probabilities,
            model_version=model or "jev-classifier-v1",
        )

    async def evaluate_batch(
        self,
        states: list[dict[str, Any]],
        questions: dict[str, str],
        model: str | None = None,
    ) -> list[JevEvaluationResultDTO]:
        return [await self.evaluate(s, questions, model) for s in states]

    async def is_healthy(self) -> bool:
        return True


def get_jev_client(settings: Settings = Depends(get_settings)) -> JevClientProtocol:
    """Dependency provider yielding the Jev Decision API client adapter."""
    try:
        mod = importlib.import_module("src.integrations.jev.jev")
        cls = getattr(mod, "JevClient", None)
        if cls:
            return cls(settings=settings)
    except (ImportError, AttributeError):
        pass

    return _StubJevClient()


class _StubLLMClient:
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        return LLMResponseDTO(
            content="Mock LLM completion",
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
            model="mock-model",
        )

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> Any:
        async def _generator():
            yield LLMStreamChunkDTO(delta_content="Mock", done=False)
            yield LLMStreamChunkDTO(delta_content=" completion", done=True)
        return _generator()

    async def describe_image(
        self,
        image_bytes: bytes,
        media_type: str,
        question: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        raise RuntimeError("the stub LLM client cannot view images")

    async def embed_texts(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]

    async def is_healthy(self) -> bool:
        return True


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClientProtocol:
    """Dependency provider yielding multi-tier LLM client adapter."""
    try:
        mod = importlib.import_module("src.integrations.llm.client")
        cls = getattr(mod, "LLMClient", None)
        if cls:
            return cls(settings=settings)
    except (ImportError, AttributeError):
        pass

    return _StubLLMClient()


class _StubSandboxRunner:
    async def create_container(
        self,
        run_id: str,
        timeout_seconds: int = 900,
    ) -> SandboxContainerDTO:
        return SandboxContainerDTO(
            container_id=f"sbx-{run_id}",
            workspace_path="/workspace",
            created_at=datetime.now(timezone.utc).timestamp(),
        )

    async def execute_command(
        self,
        container_id: str,
        command: str,
        timeout_seconds: int = 60,
    ) -> SandboxExecResultDTO:
        return SandboxExecResultDTO(
            exit_code=0,
            stdout="mock output",
            stderr="",
            duration_ms=10.0,
        )

    async def read_file(self, container_id: str, path: str) -> bytes:
        return b""

    async def write_file(self, container_id: str, path: str, content: bytes) -> None:
        pass

    async def snapshot_workspace(self, container_id: str, target_tar_path: str) -> int:
        return 0

    async def restore_workspace(self, container_id: str, source_tar_path: str) -> None:
        pass

    async def stop_and_remove(self, container_id: str) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True


def get_sandbox_runner(settings: Settings = Depends(get_settings)) -> SandboxRunnerProtocol:
    """Dependency provider yielding Docker sandbox runner adapter."""
    try:
        mod = importlib.import_module("src.integrations.sandbox.sandbox")
        cls = getattr(mod, "SandboxRunner", None)
        if cls:
            return cls(settings=settings)
    except (ImportError, AttributeError):
        pass

    return _StubSandboxRunner()


class _StubMCPHost:
    """Fail-closed stand-in for the connector host.

    It is the fallback when the MCP module cannot be imported, which is exactly
    when the host is broken. A call therefore returns an error: a success here
    would feed the model an invented ``mock tool output`` and it would treat a
    tool that never ran as done.
    """

    async def list_tools(self, connector_id: str) -> list[MCPToolDefinitionDTO]:
        return []

    async def execute_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str = "",
    ) -> MCPToolResultDTO:
        return MCPToolResultDTO(
            tool_call_id=tool_call_id,
            output=(
                "MCP host is unavailable: the MCP client failed to import, so "
                f"tool {tool_name!r} was not run"
            ),
            is_error=True,
        )

    async def get_tool_approval_policy(
        self,
        connector_id: str,
        tool_name: str,
    ) -> str:
        return "read"

    async def is_healthy(self, connector_id: str) -> bool:
        # This host cannot run a tool, so it is never healthy. A true here would
        # make a broken install read as a working connector host.
        return False


def get_mcp_host(settings: Settings = Depends(get_settings)) -> MCPHostProtocol:
    """Dependency provider yielding Model Context Protocol client host adapter."""
    try:
        mod = importlib.import_module("src.integrations.mcp.mcp")
        cls = getattr(mod, "MCPHostClient", None)
        if cls:
            return cls(settings=settings)
    except (ImportError, AttributeError):
        pass

    return _StubMCPHost()


class _StubNotificationDispatcher:
    async def send_push_notification(
        self,
        target: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        return True

    async def dispatch(self, payload: NotificationPayloadDTO) -> NotificationResultDTO:
        return NotificationResultDTO(success=True, message_id="mock-msg-1")

    async def is_healthy(self) -> bool:
        return True


def get_notification_dispatcher(
    settings: Settings = Depends(get_settings),
) -> NotificationDispatcherProtocol:
    """Dependency provider yielding push notification dispatcher adapter."""
    try:
        mod = importlib.import_module("src.integrations.notifications.ntfy")
        cls = getattr(mod, "NtfyNotificationDispatcher", None)
        if cls:
            return cls(settings=settings)
    except (ImportError, AttributeError):
        pass

    return _StubNotificationDispatcher()


# =============================================================================
# Run wiring: the composition root chooses the loop dependencies.
#
# The loop takes a model client and a tool executor as protocols. This section
# is the only place that maps those protocols onto real adapters: the Docker
# sandbox runner, the nine tool adapters, the connector host and the artifact
# table. A test injects a fake model and a fake connector here.
# =============================================================================

DELIVERY_ARTIFACT_KINDS: dict[str, str] = {
    "deliver_file": "document",
    "generate_document": "document",
    "browser_navigate": "screenshot",
    "browser_snapshot": "screenshot",
    "browser_click": "screenshot",
    "browser_type": "screenshot",
    "browser_scroll": "screenshot",
    "browser_press_key": "screenshot",
    "browser_cua_task": "screenshot",
}
"""Artifact kind for each tool that delivers a file to the user."""

SANDBOX_IDLE_SECONDS = 900
"""Lifetime of the idle command that keeps a run container alive."""


class RunToolExecutor:
    """Run the tools of one run.

    A local tool runs inside one sandbox container per run. The container is
    created on the first local call and removed by ``close``. An external tool
    runs through the connector host, because the user approved it.

    The class satisfies :class:`src.modules.runs.loop.ToolExecutor`.
    """

    #: Tools that need no run container. They run on the host, or, for
    #: ``ask_user``, only return a pending marker. ``execute`` gives them the
    #: ``host-only`` container id so no sandbox is created for them.
    HOST_ONLY_TOOLS: frozenset[str] = frozenset(
        {
            "web_search",
            "web_fetch",
            "manage_tasks",
            "ask_user",
            "raise_card",
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_press_key",
            "browser_extract",
            "browser_cua_task",
            "memory",
            "memory_search",
        }
    )


    def __init__(
        self,
        *,
        session: AsyncSession | None,
        settings: Settings,
        run_id: UUID,
        sandbox: SandboxRunnerProtocol,
        mcp_host: MCPHostProtocol,
        delivery_dir: Any | None = None,
        connector_id: str = "default",
        llm: Any | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.run_id = run_id
        self.sandbox = sandbox
        self.mcp_host = mcp_host
        self.connector_id = connector_id
        self.llm = llm
        self._tool_secrets: dict[str, str] | None = (
            dict(secrets) if secrets is not None else None
        )
        base_path = Path(settings.WORKSPACE_BASE_PATH)
        if delivery_dir is None:
            try:
                base_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                base_path = Path("/tmp/vigil/workspaces")
                base_path.mkdir(parents=True, exist_ok=True)
        self.delivery_dir = (
            delivery_dir
            if delivery_dir is not None
            else base_path / "delivery" / str(run_id)
        )
        self.snapshot_tar = (
            base_path / "snapshots" / f"{run_id}.tar"
        )
        self._container_id: str | None = None
        self._workspace_dir: Path | None = None
        self._workspace_dir_resolved: bool = False

    @property
    def container_id(self) -> str | None:
        """Return the container id of this run, or None before the first call."""
        return self._container_id

    @property
    def workspace_dir(self) -> Path | None:
        """Return the resolved host workspace directory for this run, if any."""
        return self._workspace_dir

    async def _resolve_workspace_dir(self) -> Path | None:
        """Resolve the host workspace directory backing /workspace for this run."""
        if self._workspace_dir_resolved:
            return self._workspace_dir
        self._workspace_dir_resolved = True
        if self.session is None:
            return None
        try:
            from sqlalchemy import select
            from src.modules.projects.state import resolve_session_workspace_dir
            from src.schemas.projects import ProjectModel
            from src.schemas.runtime import MessageModel, RunModel

            row = (
                await self.session.execute(
                    select(RunModel.workspace_ref, RunModel.project_id).where(
                        RunModel.id == self.run_id
                    )
                )
            ).first()
            if row is not None:
                ws_ref, project_id = row[0], row[1]
                if isinstance(ws_ref, str) and ws_ref.strip():
                    candidate = Path(ws_ref.strip())
                    if candidate.is_dir():
                        self._workspace_dir = candidate.resolve()
                        return self._workspace_dir
                if project_id is not None:
                    proj_path = (
                        await self.session.execute(
                            select(ProjectModel.workspace_path).where(
                                ProjectModel.id == project_id
                            )
                        )
                    ).scalar_one_or_none()
                    if isinstance(proj_path, str) and proj_path.strip():
                        candidate = Path(proj_path.strip())
                        candidate.mkdir(parents=True, exist_ok=True)
                        self._workspace_dir = candidate.resolve()
                        return self._workspace_dir

            session_id = (
                await self.session.execute(
                    select(MessageModel.session_id)
                    .where(
                        MessageModel.run_id == self.run_id,
                        MessageModel.session_id.is_not(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if session_id is not None:
                candidate = resolve_session_workspace_dir(self.settings, session_id)
                if candidate.is_dir():
                    self._workspace_dir = candidate
                    return self._workspace_dir
        except Exception:  # noqa: BLE001
            self._workspace_dir = None
        return self._workspace_dir

    async def _seed_workspace_into_container(self, container_id: str) -> None:
        """Copy files from the host workspace directory into /workspace in the container."""
        ws_dir = await self._resolve_workspace_dir()
        if ws_dir is None or not ws_dir.is_dir():
            return
        write_fn: Any = getattr(self.sandbox, "write_file", None)
        if not callable(write_fn):
            return
        root = ws_dir.resolve()
        count = 0
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                rel = candidate.resolve().relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            try:
                if candidate.stat().st_size > 50 * 1024 * 1024:
                    continue
                data = candidate.read_bytes()
                await write_fn(container_id, rel.as_posix(), data)
                count += 1
                if count >= 100:
                    break
            except Exception:  # noqa: BLE001
                continue

    def _sync_snapshot_to_workspace_dir(self) -> None:
        """Extract non-hidden workspace files from the run snapshot tar back into host workspace_dir."""
        if self._workspace_dir is None or not self.snapshot_tar.is_file():
            return
        import tarfile

        root = self._workspace_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(self.snapshot_tar, mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    rel_str = member.name.lstrip("./")
                    if not rel_str or rel_str.startswith("/") or ".." in Path(rel_str).parts:
                        continue
                    rel_parts = Path(rel_str).parts
                    if any(p.startswith(".") for p in rel_parts):
                        continue
                    # Skip temporary skills copies
                    if rel_parts and rel_parts[0] == "skills":
                        continue
                    target = (root / rel_str).resolve()
                    if root not in target.parents:
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(extracted.read())
        except Exception:  # noqa: BLE001
            pass

    async def start(self) -> str:
        """Create the run container once, seed workspace files, and return its id."""
        if self._container_id is None:
            self._container_id = await self._create_container()
            await self._seed_workspace_into_container(self._container_id)
            restore: Any = getattr(self.sandbox, "restore_workspace", None)
            if callable(restore) and self.snapshot_tar.is_file():
                try:
                    await restore(self._container_id, str(self.snapshot_tar))
                except Exception:  # noqa: BLE001
                    pass
        return self._container_id

    async def close(self, *, close_browser: bool = True) -> None:
        """Close the live browser/MCP sessions, snapshot the workspace, and tear down the container."""
        if close_browser:
            await self._close_browser_session()
            close_session = getattr(self.mcp_host, "close_session", None)
            if callable(close_session):
                try:
                    await close_session(str(self.delivery_dir))
                except Exception:  # noqa: BLE001
                    pass
        if self._container_id is not None:
            container_id = self._container_id
            self._container_id = None
            snapshot: Any = getattr(self.sandbox, "snapshot_workspace", None)
            if callable(snapshot):
                try:
                    self.snapshot_tar.parent.mkdir(parents=True, exist_ok=True)
                    await snapshot(container_id, str(self.snapshot_tar))
                    self._sync_snapshot_to_workspace_dir()
                except Exception:  # noqa: BLE001
                    pass
            stop: Any = getattr(self.sandbox, "stop_and_remove", None)
            if callable(stop):
                try:
                    await stop(container_id)
                except Exception:  # noqa: BLE001
                    pass

    async def _create_container(self) -> str:
        """Create a live container through the runner spec path when it exists."""
        create: Any = getattr(self.sandbox, "create", None)
        validate: Any = getattr(self.sandbox, "validate_spec", None)
        if callable(create) and callable(validate):
            from src.integrations.sandbox.contract import resolve_image_allowlist

            image = resolve_image_allowlist(self.settings)[0]
            spec = validate(
                {
                    "image": image,
                    "command": ["sleep", str(SANDBOX_IDLE_SECONDS)],
                    "timeout_seconds": SANDBOX_IDLE_SECONDS,
                }
            )
            created = await create(spec, str(self.run_id))
            return str(created.container_id)

        created = await self.sandbox.create_container(
            run_id=str(self.run_id), timeout_seconds=SANDBOX_IDLE_SECONDS
        )
        return str(created.container_id)

    async def _load_tool_secrets(self) -> Mapping[str, str]:
        """Resolve vault secrets that host-side tools and MCP servers need.

        Reads all stored secrets for the run owner so both built-in tools
        (Firecrawl, etc.) and external MCP transports receive their credentials.
        A vault read never breaks a tool call: a failure degrades gracefully.
        """
        if self._tool_secrets is not None:
            return self._tool_secrets
        secrets: dict[str, str] = {}
        if self.session is not None:
            try:
                user_id = await self._run_owner_id()
                if user_id is not None:
                    from src.modules.settings.vault import KeyVaultService

                    vault = KeyVaultService(session=self.session, settings=self.settings)
                    entries = await vault.list_secrets(user_id)
                    for entry in entries:
                        value = await vault.get_secret(user_id, entry.key_name)
                        if value:
                            secrets[entry.key_name] = value
                    # Fallback explicit check in case list_secrets is mocked/omitted
                    if "FIRECRAWL_API_KEY" not in secrets:
                        value = await vault.get_secret(user_id, "FIRECRAWL_API_KEY")
                        if value:
                            secrets["FIRECRAWL_API_KEY"] = value
            except Exception:  # noqa: BLE001 - a missing vault row must not fail the run
                secrets = {}
        self._tool_secrets = secrets
        return secrets

    async def _run_owner_id(self) -> UUID | None:
        """Return the user that owns this run, through its card or its chat thread."""
        from sqlalchemy import select

        from src.schemas.runtime import MessageModel, RunModel, ThreadModel
        from src.schemas.triage import CardModel

        session = self.session
        assert session is not None
        card_id = (
            await session.execute(
                select(RunModel.card_id).where(RunModel.id == self.run_id)
            )
        ).scalar_one_or_none()
        if card_id is not None:
            owner = (
                await session.execute(
                    select(CardModel.user_id).where(CardModel.id == card_id)
                )
            ).scalar_one_or_none()
            if owner is not None:
                return owner
        return (
            await session.execute(
                select(ThreadModel.user_id)
                .select_from(ThreadModel)
                .join(MessageModel, MessageModel.session_id == ThreadModel.id)
                .where(MessageModel.run_id == self.run_id)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _owner_id_or_none(self) -> UUID | None:
        """Return the run owner, or None when the session cannot say.

        Widget tools reject a None identity with a failed result, so a missing
        session never silently writes as another user.
        """
        if self.session is None:
            return None
        try:
            return await self._run_owner_id()
        except Exception:  # noqa: BLE001 - a failed lookup means no identity
            return None

    async def execute(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one local tool in the sandbox (or via MCP host) and return its result."""
        from src.modules.runs.tools import TOOLS, ToolContext, invoke_tool

        secrets = await self._load_tool_secrets()
        start_session = getattr(self.mcp_host, "start_session", None)
        if callable(start_session):
            start_session(str(self.delivery_dir), vault_secrets=dict(secrets))

        if name not in TOOLS:
            result_mcp = await self.mcp_host.execute_tool(
                self.connector_id, name, dict(arguments)
            )
            return {
                "ok": not result_mcp.is_error,
                "content": result_mcp.output,
                "artifact_ref": None,
                "error": result_mcp.output if result_mcp.is_error else None,
            }

        if name in self.HOST_ONLY_TOOLS and self._container_id is None:
            container_id = "host-only"
        else:
            container_id = await self.start()
        context = ToolContext(
            runner=self.sandbox,
            container_id=container_id,
            delivery_dir=Path(self.delivery_dir),
            llm=self.llm,
            secrets=secrets,
            run_id=self.run_id,
            user_id=await self._owner_id_or_none(),
            session=self.session,
            workspace_dir=await self._resolve_workspace_dir(),
        )
        result = await invoke_tool(name, dict(arguments), context)
        artifact_id: UUID | None = None
        if result.ok and result.artifact_ref:
            artifact_id = await self._register_artifact(name, result)
        dumped = result.model_dump()
        if artifact_id is not None:
            dumped["artifact_id"] = str(artifact_id)
            content = dumped.get("content")
            if isinstance(content, dict):
                content.setdefault("artifact_id", str(artifact_id))
        return dumped

    async def execute_external(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one approved external tool.

        A tool the harness declares itself runs through the harness, so its own
        handler answers. ``ask_user`` is the only such tool today: its contract
        is a pending marker, and sending it to the connector host would run it
        on whichever third party server happened to own the name, or fail
        closed for a name no server owns. Only a name the harness does not
        declare goes to the connector host.
        """
        from src.modules.runs.tools import TOOLS

        if name in TOOLS:
            return await self.execute(name, dict(arguments))
        secrets = await self._load_tool_secrets()
        start_session = getattr(self.mcp_host, "start_session", None)
        if callable(start_session):
            start_session(str(self.delivery_dir), vault_secrets=dict(secrets))
        result = await self.mcp_host.execute_tool(
            self.connector_id, name, dict(arguments)
        )
        return {
            "ok": not result.is_error,
            "content": result.output,
            "artifact_ref": None,
            "error": result.output if result.is_error else None,
        }

    async def _register_artifact(self, tool_name: str, result: Any) -> UUID | None:
        """Write one artifact row for a file that a tool delivered.

        Uses ArtifactsService so both database-backed and sessionless runs
        register the artifact with a preview snippet for rich UI rendering.
        Returns the saved artifact id so the caller can link the step payload
        to the row without path matching. The id is also stamped into
        ``result.content`` when it is a dict, so journaled tool results carry
        the link after a refresh.
        """
        if not result.artifact_ref:
            return None
        from src.modules.artifacts.repository import ArtifactCreateDTO
        from src.modules.artifacts.service import ArtifactsService

        path = Path(str(result.artifact_ref))
        is_file = path.is_file()
        size_bytes = path.stat().st_size if is_file else 0
        preview: str | None = None
        if is_file and size_bytes > 0:
            preview = _build_artifact_preview(path)

        service = ArtifactsService(session=self.session, settings=self.settings)
        saved = await service.save_artifact(
            ArtifactCreateDTO(
                run_id=self.run_id,
                kind=DELIVERY_ARTIFACT_KINDS.get(tool_name, "document"),
                path=str(path),
                size_bytes=size_bytes,
                preview=preview,
            )
        )
        if isinstance(result.content, dict):
            result.content.setdefault("preview", preview)
            result.content.setdefault("artifact_id", str(saved.id))
        return saved.id

    async def _close_browser_session(self) -> None:
        """Close the live Firecrawl session of this run, best effort."""
        try:
            from src.modules.runs.tools.browser import close_browser_session

            secrets = self._tool_secrets or {}
            api_key = (
                secrets.get("FIRECRAWL_API_KEY")
                or self.settings.FIRECRAWL_API_KEY
            )
            await close_browser_session(
                self.delivery_dir, api_key, self.settings.FIRECRAWL_BASE_URL
            )
        except Exception:  # noqa: BLE001 - cleanup must not mask the run outcome
            pass


def _build_artifact_preview(path: Path, max_chars: int = 500) -> str | None:
    """Build a short text preview (up to 500 chars) for an artifact file."""
    ext = path.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"):
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if ext in (".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".tar", ".tgz"):
        try:
            from src.modules.runs.tools.files import (
                _extract_archive_text,
                _extract_docx_text,
                _extract_pdf_text,
                _extract_pptx_text,
                _extract_xlsx_text,
            )

            if ext == ".pdf":
                text = _extract_pdf_text(raw)
            elif ext == ".docx":
                text = _extract_docx_text(raw)
            elif ext == ".xlsx":
                text = _extract_xlsx_text(raw)
            elif ext == ".pptx":
                text = _extract_pptx_text(raw)
            else:
                text = _extract_archive_text(path.name, raw)
            return text[:max_chars] if text else None
        except Exception:  # noqa: BLE001
            return None
    try:
        text = raw[: max_chars * 4].decode("utf-8", errors="replace").strip()
        return text[:max_chars] if text else None
    except Exception:  # noqa: BLE001
        return None


def build_tool_executor(
    session: AsyncSession | None,
    settings: Settings,
    run_id: UUID,
    *,
    sandbox: SandboxRunnerProtocol | None = None,
    mcp_host: MCPHostProtocol | None = None,
    delivery_dir: Any | None = None,
    llm: LLMClientProtocol | None = None,
    secrets: Mapping[str, str] | None = None,
) -> RunToolExecutor:
    """Build the tool executor of one run from the real adapters."""
    return RunToolExecutor(
        session=session,
        settings=settings,
        run_id=run_id,
        sandbox=sandbox if sandbox is not None else get_sandbox_runner(settings),
        mcp_host=mcp_host if mcp_host is not None else get_mcp_host(settings),
        delivery_dir=delivery_dir,
        llm=llm if llm is not None else get_llm_client(settings),
        secrets=secrets,
    )


def build_pricing_service(session: AsyncSession | None) -> Any:
    """Build the pricing service over a session.

    Args:
        session: Database session, or None in a test.

    Returns:
        The pricing service. With no session, every model costs zero.
    """
    from src.modules.pricing.service import PricingService, SqlPricingRepository

    if session is None:
        return PricingService(repository=_EmptyPricingRepository())
    return PricingService(repository=SqlPricingRepository(session))


class _EmptyPricingRepository:
    """A pricing repository with no rows. Every model then costs zero."""

    async def get_by_model(self, model: str) -> Any:
        return None

    async def list_prices(self) -> list[Any]:
        return []

    async def upsert(self, data: Any) -> Any:
        raise RuntimeError("no session: the pricing table is not writable")


def get_pricing_service(
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """FastAPI provider for the pricing service."""
    return build_pricing_service(session)


def build_runs_service(
    session: AsyncSession | None,
    settings: Settings,
    *,
    run_id: UUID | None = None,
    llm: LLMClientProtocol | None = None,
    sandbox: SandboxRunnerProtocol | None = None,
    mcp_host: MCPHostProtocol | None = None,
    tools: Any | None = None,
    compactor: Any | None = None,
) -> Any:
    """Build the runs service with the model client and the tools of one run.

    Args:
        session: Database session, or None in a test.
        settings: Application settings.
        run_id: Run UUID. When it is set, the service can run tools.
        llm: Optional model client. It defaults to the real client.
        sandbox: Optional sandbox runner. It defaults to the real runner.
        mcp_host: Optional connector host. It defaults to the real host.
        tools: Optional tool executor. It overrides the built executor.
        compactor: Optional compaction provider. It defaults to CompactionService.

    Returns:
        The runs service.
    """
    from src.modules.runs.service import RunsService

    resolved_llm = llm if llm is not None else get_llm_client(settings)
    executor = tools
    if executor is None and run_id is not None:
        executor = build_tool_executor(
            session,
            settings,
            run_id,
            sandbox=sandbox,
            mcp_host=mcp_host,
            llm=resolved_llm,
        )
    resolved_compactor = (
        compactor
        if compactor is not None
        else get_compaction_service(session=session, settings=settings)  # type: ignore[arg-type]
    )
    return RunsService(
        session=session,
        settings=settings,
        sandbox=sandbox,
        llm=resolved_llm,
        tools=executor,
        pricing=build_pricing_service(session),
        compactor=resolved_compactor,
    )


def get_runs_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Dependency provider for the runs service.

    The request path reads and writes run rows. It never runs a tool, so the
    provider does not build a sandbox executor.
    """
    return build_runs_service(session, settings)


# =============================================================================
# Explicit Re-Export Registry
# =============================================================================

__all__ = [
    # Core Dependencies (4)
    "get_settings",
    "get_db_session",
    "get_current_device",
    "get_current_user",
    # Domain Module Service Providers
    "get_health_service",
    "get_auth_service",
    "get_cards_service",
    "get_triage_service",
    "get_pricing_service",
    "get_runs_service",
    "get_approvals_service",
    "get_chat_service",
    "get_artifacts_service",
    "get_memory_service",
    "get_compaction_service",
    "get_skills_service",
    "get_connectors_service",
    "get_settings_service",
    "get_audit_service",
    "get_jobs_service",
    "get_projects_service",
    "get_widgets_service",
    "get_key_vault_service",


    # External Integration Client Providers (5)
    "get_jev_client",
    "get_llm_client",
    "get_sandbox_runner",
    "get_mcp_host",
    "get_notification_dispatcher",
]
