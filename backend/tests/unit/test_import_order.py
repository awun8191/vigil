"""Regression tests for import order independence.

Phase 4 added an import cycle between the runs package and the approvals
package. The cycle only showed in a new interpreter. The full test suite loaded
a different module first, so the tests stayed green. These tests start a new
interpreter and check the deployed entry points directly.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import src.config.router as router_module
from src.config.settings import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
"""Directory that holds the ``src`` package."""

REQUIRED_PATHS = (
    "/api/v1/runs",
    "/api/v1/runs/{run_id}/events",
    "/api/v1/approvals/{approval_id}/decision",
)
"""Run and approval routes that the served app must publish."""

SERVED_APP_PROBE = """
import json

import src.main

required = [
    "/api/v1/runs",
    "/api/v1/runs/{run_id}/events",
    "/api/v1/approvals/{approval_id}/decision",
]
served = sorted(src.main.app.openapi()["paths"])
created = sorted(src.main.create_app(src.main.get_settings()).openapi()["paths"])

payload = {
    "served_app_path_count": len(served),
    "created_app_path_count": len(created),
    "served_app_paths": served,
    "created_app_paths": created,
    "required_paths": required,
}
print("PROBE_PAYLOAD " + json.dumps(payload))
"""
"""Probe that imports only ``src.main`` and reports the mounted paths."""


def run_fresh_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    """Run one code snippet in a new interpreter with no PYTHONPATH."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_worker_imports_in_fresh_interpreter() -> None:
    """A new interpreter can import the worker module. No other src module loads first."""
    result = run_fresh_interpreter("import src.worker")

    assert result.returncode == 0, result.stderr
    assert "ImportError" not in result.stderr


def test_served_app_mounts_run_routes_in_fresh_interpreter() -> None:
    """The app that src.main serves publishes 46 paths. The run routes are in that set.

    The count is pinned, so an accidental route change fails this test. It was 44
    before the phase 1 security merge. The served app publishes 46 now.
    """
    result = run_fresh_interpreter(SERVED_APP_PROBE)

    assert result.returncode == 0, result.stderr

    payload_line = next(
        line for line in result.stdout.splitlines() if line.startswith("PROBE_PAYLOAD ")
    )
    payload = json.loads(payload_line.removeprefix("PROBE_PAYLOAD "))

    assert payload["served_app_path_count"] == 46, payload["served_app_paths"]
    assert payload["created_app_path_count"] == 46, payload["created_app_paths"]
    for path in payload["required_paths"]:
        assert path in payload["served_app_paths"], path
        assert path in payload["created_app_paths"], path


def test_package_reexports_stay_available() -> None:
    """Package level re-exports stay importable after the initializer change."""
    import src.modules.approvals as approvals
    import src.modules.runs as runs

    assert approvals.ApprovalsService.__name__ == "ApprovalsService"
    assert approvals.get_approvals_service is not None
    assert runs.RunsService.__name__ == "RunsService"
    assert runs.get_runs_service is not None
    assert runs.build_runs_router is not None
    assert set(runs.__all__) <= set(dir(runs))
    assert set(approvals.__all__) <= set(dir(approvals))

    with pytest.raises(AttributeError):
        _ = runs.not_a_public_name


def test_failed_module_import_raises_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed module import stops the factory. The factory does not skip the module."""
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "src.modules.runs.router":
            raise ImportError("simulated incomplete module")
        return real_import_module(name, package)

    monkeypatch.setattr(
        router_module, "importlib", types.SimpleNamespace(import_module=fake_import_module)
    )

    # The mock records the error call directly. An in-process alembic run calls
    # logging.fileConfig. That call disables the existing loggers. A log
    # handler can miss the record in the full test suite.
    logged: list[str] = []
    monkeypatch.setattr(
        router_module.logger,
        "error",
        lambda message, *args, **kwargs: logged.append(message % args),
    )

    with pytest.raises(router_module.RouterMountError):
        router_module.create_api_router(Settings())

    assert any("src.modules.runs.router" in message for message in logged), logged


def test_missing_router_factory_raises_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module without its factory stops the factory."""
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "src.modules.jobs.router":
            return types.ModuleType(name)
        return real_import_module(name, package)

    monkeypatch.setattr(
        router_module, "importlib", types.SimpleNamespace(import_module=fake_import_module)
    )

    with pytest.raises(router_module.RouterMountError):
        router_module.create_api_router(Settings())
