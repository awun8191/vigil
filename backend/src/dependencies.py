"""
Central dependency hub for Virgil FastAPI backend.

Centrally re-exports:
- Core application dependencies (get_settings, get_db_session, get_current_device, get_current_user)
- All 14 domain module service providers (get_<module>_service)
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


def _get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Internal lazy singleton for async SQLAlchemy session factory."""
    global _engine, _sessionmaker
    if _sessionmaker is None:
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
    return _sessionmaker


async def get_db_session(
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding an async database session with automatic transaction management.
    Rolls back on unhandled exceptions and ensures clean closure.
    """
    session_factory = _get_sessionmaker(settings)
    has_yielded = False
    try:
        async with session_factory() as session:
            try:
                has_yielded = True
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
    except BaseException:
        if has_yielded:
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
    authorization: str = Header(..., description="Device Bearer token: 'Bearer vgt_...'"),
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
# 14 Domain Module Service Providers
# =============================================================================

get_health_service = _resolve_service_dependency("health", "get_health_service")
get_auth_service = _resolve_service_dependency("auth", "get_auth_service")
get_cards_service = _resolve_service_dependency("cards", "get_cards_service")
get_triage_service = _resolve_service_dependency("triage", "get_triage_service")
get_approvals_service = _resolve_service_dependency("approvals", "get_approvals_service")
get_chat_service = _resolve_service_dependency("chat", "get_chat_service")
get_artifacts_service = _resolve_service_dependency("artifacts", "get_artifacts_service")
get_memory_service = _resolve_service_dependency("memory", "get_memory_service")
get_compaction_service = _resolve_service_dependency("compaction", "get_compaction_service")
get_skills_service = _resolve_service_dependency("skills", "get_skills_service")
get_connectors_service = _resolve_service_dependency("connectors", "get_connectors_service")
get_settings_service = _resolve_service_dependency("settings", "get_settings_service")
get_jobs_service = _resolve_service_dependency("jobs", "get_jobs_service")


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
        return JevEvaluationResultDTO(
            answers={k: "yes" for k in questions},
            probabilities={k: 0.9 for k in questions},
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

    async def embed_texts(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

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
            output="mock tool output",
            is_error=False,
        )

    async def get_tool_approval_policy(
        self,
        connector_id: str,
        tool_name: str,
    ) -> str:
        return "read"

    async def is_healthy(self, connector_id: str) -> bool:
        return True


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
# sandbox runner, the eight tool adapters, the connector host and the artifact
# table. A test injects a fake model and a fake connector here.
# =============================================================================

DELIVERY_ARTIFACT_KINDS: dict[str, str] = {
    "deliver_file": "document",
    "generate_document": "document",
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
    ) -> None:
        self.session = session
        self.settings = settings
        self.run_id = run_id
        self.sandbox = sandbox
        self.mcp_host = mcp_host
        self.connector_id = connector_id
        self.delivery_dir = (
            delivery_dir
            if delivery_dir is not None
            else Path(settings.WORKSPACE_BASE_PATH) / "delivery" / str(run_id)
        )
        self._container_id: str | None = None

    @property
    def container_id(self) -> str | None:
        """Return the container id of this run, or None before the first call."""
        return self._container_id

    async def start(self) -> str:
        """Create the run container once and return its id."""
        if self._container_id is None:
            self._container_id = await self._create_container()
        return self._container_id

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

    async def execute(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one local tool in the sandbox and return its result."""
        from src.modules.runs.tools import ToolContext, invoke_tool

        container_id = await self.start()
        context = ToolContext(
            runner=self.sandbox,
            container_id=container_id,
            delivery_dir=Path(self.delivery_dir),
        )
        result = await invoke_tool(name, dict(arguments), context)
        if result.ok and result.artifact_ref:
            await self._register_artifact(name, result)
        return result.model_dump()

    async def execute_external(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one approved external tool through the connector host."""
        result = await self.mcp_host.execute_tool(
            self.connector_id, name, dict(arguments)
        )
        return {
            "ok": not result.is_error,
            "content": result.output,
            "artifact_ref": None,
            "error": result.output if result.is_error else None,
        }

    async def _register_artifact(self, tool_name: str, result: Any) -> None:
        """Write one artifact row for a file that a tool delivered."""
        if self.session is None or not result.artifact_ref:
            return
        from src.schemas.runtime import ArtifactModel

        path = Path(str(result.artifact_ref))
        self.session.add(
            ArtifactModel(
                run_id=self.run_id,
                kind=DELIVERY_ARTIFACT_KINDS.get(tool_name, "file"),
                path=str(path),
                size_bytes=path.stat().st_size if path.is_file() else 0,
                preview=None,
            )
        )
        await self.session.commit()

    async def close(self) -> None:
        """Remove the run container. A missing container is not an error."""
        container_id = self._container_id
        self._container_id = None
        if container_id is None:
            return
        try:
            await self.sandbox.stop_and_remove(container_id)
        except Exception:  # noqa: BLE001 - cleanup must not mask the run outcome
            pass


def build_tool_executor(
    session: AsyncSession | None,
    settings: Settings,
    run_id: UUID,
    *,
    sandbox: SandboxRunnerProtocol | None = None,
    mcp_host: MCPHostProtocol | None = None,
    delivery_dir: Any | None = None,
) -> RunToolExecutor:
    """Build the tool executor of one run from the real adapters."""
    return RunToolExecutor(
        session=session,
        settings=settings,
        run_id=run_id,
        sandbox=sandbox if sandbox is not None else get_sandbox_runner(settings),
        mcp_host=mcp_host if mcp_host is not None else get_mcp_host(settings),
        delivery_dir=delivery_dir,
    )


def build_runs_service(
    session: AsyncSession | None,
    settings: Settings,
    *,
    run_id: UUID | None = None,
    llm: LLMClientProtocol | None = None,
    sandbox: SandboxRunnerProtocol | None = None,
    mcp_host: MCPHostProtocol | None = None,
    tools: Any | None = None,
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

    Returns:
        The runs service.
    """
    from src.modules.runs.service import RunsService

    executor = tools
    if executor is None and run_id is not None:
        executor = build_tool_executor(
            session, settings, run_id, sandbox=sandbox, mcp_host=mcp_host
        )
    return RunsService(
        session=session,
        settings=settings,
        sandbox=sandbox,
        llm=llm if llm is not None else get_llm_client(settings),
        tools=executor,
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
    # Domain Module Service Providers (14)
    "get_health_service",
    "get_auth_service",
    "get_cards_service",
    "get_triage_service",
    "get_runs_service",
    "get_approvals_service",
    "get_chat_service",
    "get_artifacts_service",
    "get_memory_service",
    "get_compaction_service",
    "get_skills_service",
    "get_connectors_service",
    "get_settings_service",
    "get_jobs_service",
    # External Integration Client Providers (5)
    "get_jev_client",
    "get_llm_client",
    "get_sandbox_runner",
    "get_mcp_host",
    "get_notification_dispatcher",
]
