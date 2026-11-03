"""
Unit tests for the central dependency hub (src/dependencies.py).

Invariants Enforced:
1. __all__ explicitly defines exactly the expected 23 dependency providers.
2. Every symbol exported in __all__ exists and is callable.
3. get_settings() returns a valid Settings instance.
4. get_db_session() yields an AsyncSession (or valid session protocol).
5. All 5 integration client providers satisfy their respective @runtime_checkable protocols.
6. All 14 domain service providers instantiate and return services satisfying their module repository protocols.
7. get_current_device and get_current_user auth dependencies behave correctly.
"""
from __future__ import annotations

import inspect
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
import src.dependencies as deps
from src.config.exceptions import UnauthorizedError
from src.config.settings import Settings
from src.integrations.repositories import (
    JevClientProtocol,
    LLMClientProtocol,
    MCPHostProtocol,
    NotificationDispatcherProtocol,
    SandboxRunnerProtocol,
)
from src.modules.auth.repository import DeviceDTO, UserDTO
from src.modules.health.repository import HealthRepository
from src.modules.auth.repository import AuthRepository
from src.modules.cards.repository import CardsRepository
from src.modules.triage.repository import TriageRepository
from src.modules.runs.repository import RunsRepository
from src.modules.approvals.repository import ApprovalsRepository
from src.modules.chat.repository import ChatRepository
from src.modules.artifacts.repository import ArtifactsRepository
from src.modules.memory.repository import MemoryRepository
from src.modules.compaction.repository import CompactionRepository
from src.modules.skills.repository import SkillsRepository
from src.modules.connectors.repository import ConnectorsRepository
from src.modules.settings.repository import SettingsRepository
from src.modules.jobs.repository import JobsRepository

EXPECTED_PROVIDERS = [
    # Core Dependencies (4)
    "get_settings",
    "get_db_session",
    "get_current_device",
    "get_current_user",
    # Domain services (15)
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
    # External integrations (5)
    "get_jev_client",
    "get_llm_client",
    "get_sandbox_runner",
    "get_mcp_host",
    "get_notification_dispatcher",
]


def test_dependencies_all_exports_the_expected_providers() -> None:
    """Verify that src.dependencies.__all__ holds exactly the expected providers."""
    assert hasattr(deps, "__all__"), "src.dependencies must define __all__"
    assert len(deps.__all__) == len(EXPECTED_PROVIDERS), (
        f"Expected {len(EXPECTED_PROVIDERS)} exports, got {len(deps.__all__)}: {deps.__all__}"
    )
    assert set(deps.__all__) == set(EXPECTED_PROVIDERS)


def test_every_exported_provider_is_callable() -> None:
    """Verify every symbol exported in __all__ exists and is callable."""
    for name in deps.__all__:
        assert hasattr(deps, name), f"src.dependencies is missing exported symbol '{name}'"
        obj = getattr(deps, name)
        assert callable(obj), f"Exported symbol '{name}' in src.dependencies must be callable"


def test_get_settings_provider() -> None:
    """Verify get_settings returns a Settings instance."""
    settings = deps.get_settings()
    assert isinstance(settings, Settings)
    assert hasattr(settings, "DATABASE_URL")
    assert hasattr(settings, "API_V1_PREFIX")


@pytest.mark.asyncio
async def test_get_db_session_provider() -> None:
    """Verify get_db_session yields an AsyncSession (or fallback session protocol)."""
    settings = Settings()
    gen = deps.get_db_session(settings=settings)
    session = await anext(gen)
    try:
        assert isinstance(session, AsyncSession) or hasattr(session, "execute")
    finally:
        await gen.aclose()


def test_integration_client_providers_satisfy_protocols() -> None:
    """Verify all 5 external integration providers satisfy their @runtime_checkable protocols."""
    settings = Settings()

    jev = deps.get_jev_client(settings=settings)
    assert isinstance(jev, JevClientProtocol), "get_jev_client must satisfy JevClientProtocol"

    llm = deps.get_llm_client(settings=settings)
    assert isinstance(llm, LLMClientProtocol), "get_llm_client must satisfy LLMClientProtocol"

    runner = deps.get_sandbox_runner(settings=settings)
    assert isinstance(runner, SandboxRunnerProtocol), "get_sandbox_runner must satisfy SandboxRunnerProtocol"

    mcp = deps.get_mcp_host(settings=settings)
    assert isinstance(mcp, MCPHostProtocol), "get_mcp_host must satisfy MCPHostProtocol"

    notif = deps.get_notification_dispatcher(settings=settings)
    assert isinstance(notif, NotificationDispatcherProtocol), "get_notification_dispatcher must satisfy NotificationDispatcherProtocol"


DOMAIN_SERVICE_SPECS = [
    ("get_health_service", HealthRepository),
    ("get_auth_service", AuthRepository),
    ("get_cards_service", CardsRepository),
    ("get_triage_service", TriageRepository),
    ("get_runs_service", RunsRepository),
    ("get_approvals_service", ApprovalsRepository),
    ("get_chat_service", ChatRepository),
    ("get_artifacts_service", ArtifactsRepository),
    ("get_memory_service", MemoryRepository),
    ("get_compaction_service", CompactionRepository),
    ("get_skills_service", SkillsRepository),
    ("get_connectors_service", ConnectorsRepository),
    ("get_settings_service", SettingsRepository),
    ("get_jobs_service", JobsRepository),
]


@pytest.mark.parametrize("provider_name,protocol_cls", DOMAIN_SERVICE_SPECS)
def test_domain_service_providers_signature_and_protocol(provider_name: str, protocol_cls: type) -> None:
    """Verify each domain service provider accepts (session, settings) and returns a service satisfying its protocol."""
    provider = getattr(deps, provider_name)
    sig = inspect.signature(provider)
    param_names = list(sig.parameters.keys())
    assert "session" in param_names, f"{provider_name} must accept 'session' parameter"
    assert "settings" in param_names, f"{provider_name} must accept 'settings' parameter"

    # Instantiate provider with dummy/fallback session and settings
    settings = Settings()
    dummy_session = None
    service = provider(session=dummy_session, settings=settings)

    # Must satisfy runtime_checkable protocol
    assert isinstance(service, protocol_cls), (
        f"Service returned by {provider_name} ({type(service).__name__}) "
        f"does not satisfy protocol {protocol_cls.__name__}"
    )


@pytest.mark.asyncio
async def test_auth_dependencies() -> None:
    """Verify get_current_device and get_current_user dependencies."""
    settings = Settings()
    device = await deps.get_current_device(authorization="Bearer vgt_test123", settings=settings, session=None)
    assert isinstance(device, DeviceDTO)
    assert device.name == "Test Device"

    # Empty token raises UnauthorizedError
    with pytest.raises(UnauthorizedError):
        await deps.get_current_device(authorization="Bearer ", settings=settings, session=None)

    user = await deps.get_current_user(device=device, settings=settings, session=None)
    assert isinstance(user, UserDTO)
    assert user.display_name == "Default User"
