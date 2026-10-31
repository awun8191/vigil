"""Unit tests for root API router factory and factory pattern compliance."""
import ast
from pathlib import Path
import pytest
from fastapi import APIRouter
from src.config.router import MODULE_FACTORIES, create_api_router
from src.config.settings import Settings


def test_create_api_router_returns_apirouter() -> None:
    """Verify factory returns an APIRouter with default API_V1_PREFIX."""
    settings = Settings()
    router = create_api_router(settings)
    assert isinstance(router, APIRouter)
    assert router.prefix == settings.API_V1_PREFIX


def test_create_api_router_honors_custom_prefix() -> None:
    """Verify factory honors custom prefix configured in settings."""
    settings = Settings(API_V1_PREFIX="/custom/api/v2")
    router = create_api_router(settings)
    assert router.prefix == "/custom/api/v2"


def test_module_factories_list_covers_all_14_modules() -> None:
    """Verify all 14 domain modules are accounted for in MODULE_FACTORIES."""
    expected_modules = {
        "health",
        "auth",
        "cards",
        "triage",
        "runs",
        "approvals",
        "chat",
        "artifacts",
        "memory",
        "compaction",
        "skills",
        "connectors",
        "settings",
        "jobs",
    }
    registered_modules = {mod for mod, _ in MODULE_FACTORIES}
    assert registered_modules == expected_modules
    assert len(MODULE_FACTORIES) == 14


def test_no_bare_global_routers_in_config() -> None:
    """Architectural invariant test: config/router.py must not define a global router = APIRouter()."""
    router_file = Path(__file__).parents[2] / "src" / "config" / "router.py"
    tree = ast.parse(router_file.read_text(encoding="utf-8"))

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "router":
                    pytest.fail("Found bare global 'router = ...' assignment in src/config/router.py")
