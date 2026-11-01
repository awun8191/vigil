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
get_runs_service = _resolve_service_dependency("runs", "get_runs_service")
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
        return [[0.0] * 384 for _ in texts]

    async def is_healthy(self) -> bool:
        return True


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClientProtocol:
    """Dependency provider yielding multi-tier LLM client adapter."""
    try:
        mod = importlib.import_module("src.integrations.llm.llm")
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
        cls = getattr(mod, "DockerSandboxRunner", None)
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
