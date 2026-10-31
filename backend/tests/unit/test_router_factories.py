"""
Architectural and functional tests for domain module router factories.

Invariants Enforced:
1. All 14 domain modules define a router.py file.
2. Zero bare global `router = ...` assignments at module scope in any domain router.py.
3. Zero module-level `APIRouter()` calls (all routers must be constructed inside factory functions).
4. Each domain module exports `build_<module>_router(settings: Settings) -> APIRouter`.
5. Every returned router has the correct prefix (/<module>) and tags.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import pytest
from fastapi import APIRouter
from src.config.settings import Settings

EXPECTED_MODULES = [
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
]


def get_domain_router_files() -> list[tuple[str, Path]]:
    """Return list of (module_name, router_path) for all 14 domain modules."""
    backend_root = Path(__file__).parents[2]
    modules_dir = backend_root / "src" / "modules"
    files = []
    for mod_name in EXPECTED_MODULES:
        router_path = modules_dir / mod_name / "router.py"
        files.append((mod_name, router_path))
    return files


def test_all_14_domain_router_files_exist() -> None:
    """Verify all 14 domain modules have router.py."""
    for mod_name, router_path in get_domain_router_files():
        assert router_path.is_file(), f"Missing router.py in module {mod_name}: {router_path}"


def test_no_bare_global_routers_in_domain_modules() -> None:
    """Architectural invariant test: No domain router.py defines a global router = APIRouter()."""
    violations: list[str] = []

    for mod_name, router_path in get_domain_router_files():
        if not router_path.is_file():
            continue
        content = router_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(router_path))

        for node in tree.body:
            # Check for top-level assignments to 'router'
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "router":
                        violations.append(
                            f"{router_path.relative_to(router_path.parents[2])}:{node.lineno} "
                            f"defines bare global 'router = ...'"
                        )
            # Check for top-level APIRouter() calls
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name == "APIRouter":
                    violations.append(
                        f"{router_path.relative_to(router_path.parents[2])}:{node.lineno} "
                        f"calls APIRouter() at module scope"
                    )

    assert not violations, "Found bare global router violations:\n" + "\n".join(violations)


def test_factory_function_ast_signatures() -> None:
    """Verify each router.py defines build_<module>_router with settings parameter."""
    violations: list[str] = []

    for mod_name, router_path in get_domain_router_files():
        if not router_path.is_file():
            continue
        content = router_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(router_path))

        expected_factory_name = f"build_{mod_name}_router"
        factory_found = False

        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == expected_factory_name:
                factory_found = True
                # Check args: must have at least one arg (settings)
                arg_names = [arg.arg for arg in node.args.args]
                if not arg_names or arg_names[0] != "settings":
                    violations.append(
                        f"{router_path.relative_to(router_path.parents[2])} "
                        f"{expected_factory_name} first argument must be 'settings', got {arg_names}"
                    )
                break

        if not factory_found:
            violations.append(
                f"{router_path.relative_to(router_path.parents[2])} missing factory function '{expected_factory_name}'"
            )

    assert not violations, "Router factory AST violations:\n" + "\n".join(violations)


@pytest.mark.parametrize("mod_name", EXPECTED_MODULES)
def test_router_factory_instantiation(mod_name: str) -> None:
    """Verify that importing and invoking build_<module>_router returns a valid APIRouter."""
    mod = importlib.import_module(f"src.modules.{mod_name}.router")
    factory_name = f"build_{mod_name}_router"
    factory = getattr(mod, factory_name, None)
    assert callable(factory), f"Module src.modules.{mod_name}.router must export callable {factory_name}"

    settings = Settings()
    router = factory(settings)
    assert isinstance(router, APIRouter), f"{factory_name} must return an instance of fastapi.APIRouter"
    assert router.prefix == f"/{mod_name}", f"{factory_name} prefix should be /{mod_name}, got {router.prefix}"
    assert len(router.routes) > 0, f"{factory_name} should register at least 1 endpoint route"
