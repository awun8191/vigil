"""Root API router factory aggregating domain module sub-routers under /api/v1 prefix."""
from __future__ import annotations

import importlib
import logging
from typing import Callable
from fastapi import APIRouter

from src.config.settings import Settings

logger = logging.getLogger("vigil.router")


class RouterMountError(RuntimeError):
    """Raised when a listed module router cannot be imported or built.

    The factory fails at startup on purpose. A silent skip removes a module API
    without a message. That defect is not easy to see. The tests stay green in a
    different import sequence.
    """


# Mapping of module names to their factory function names
MODULE_FACTORIES: list[tuple[str, str]] = [
    ("health", "build_health_router"),
    ("auth", "build_auth_router"),
    ("cards", "build_cards_router"),
    ("triage", "build_triage_router"),
    ("runs", "build_runs_router"),
    ("approvals", "build_approvals_router"),
    ("chat", "build_chat_router"),
    ("artifacts", "build_artifacts_router"),
    ("memory", "build_memory_router"),
    ("compaction", "build_compaction_router"),
    ("skills", "build_skills_router"),
    ("connectors", "build_connectors_router"),
    ("settings", "build_settings_router"),
    ("jobs", "build_jobs_router"),
]


def create_api_router(settings: Settings) -> APIRouter:
    """
    Root API router factory aggregating all 14 domain module router builders.
    Enforces dynamic configuration binding and prevents global router state leaks.

    A missing module router is a startup error. The factory never skips a
    listed module.

    Args:
        settings: Application Settings instance.

    Returns:
        APIRouter with prefix set to settings.API_V1_PREFIX and all 14 sub-routers mounted.

    Raises:
        RouterMountError: If a module router cannot be imported or has no factory.
    """
    api_router = APIRouter(prefix=settings.API_V1_PREFIX)

    for module_name, factory_name in MODULE_FACTORIES:
        module_path = f"src.modules.{module_name}.router"
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            logger.error("Module router import failed: %s (%s)", module_path, exc, exc_info=True)
            raise RouterMountError(
                f"Cannot import router module {module_path}: {exc}"
            ) from exc

        builder: Callable[[Settings], APIRouter] | None = getattr(mod, factory_name, None)
        if builder is None or not callable(builder):
            logger.error("Router factory %s is missing from %s", factory_name, module_path)
            raise RouterMountError(
                f"Module {module_path} has no callable factory {factory_name}"
            )

        api_router.include_router(builder(settings))
        logger.debug("Mounted %s router from %s", module_name, module_path)

    return api_router
