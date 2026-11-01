"""Root API router factory aggregating domain module sub-routers under /api/v1 prefix."""
from __future__ import annotations

import importlib
import logging
from typing import Callable
from fastapi import APIRouter

from src.config.settings import Settings

logger = logging.getLogger("vigil.router")

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

    Args:
        settings: Application Settings instance.

    Returns:
        APIRouter with prefix set to settings.API_V1_PREFIX and all available sub-routers mounted.
    """
    api_router = APIRouter(prefix=settings.API_V1_PREFIX)

    for module_name, factory_name in MODULE_FACTORIES:
        module_path = f"src.modules.{module_name}.router"
        try:
            mod = importlib.import_module(module_path)
            builder: Callable[[Settings], APIRouter] | None = getattr(mod, factory_name, None)
            if builder and callable(builder):
                sub_router = builder(settings)
                api_router.include_router(sub_router)
                logger.debug("Mounted %s router from %s", module_name, module_path)
            else:
                logger.debug("Router factory %s not found in %s", factory_name, module_path)
        except (ImportError, AttributeError) as exc:
            # Allows Milestone 2 testing before Milestone 3 domain skeletons are implemented
            logger.debug("Module router not loaded: %s (%s)", module_path, exc)

    return api_router
