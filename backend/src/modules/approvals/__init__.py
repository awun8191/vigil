"""Approvals domain module.

The package loads its public names when code asks for them. An eager import is
not safe here. An import cycle goes from ``approvals.repository`` to
``approvals.dependencies``, then to ``approvals.service``, and back to
``runs.service``. That cycle starts when a package import loads a sibling
module. This file uses the module ``__getattr__`` hook that PEP 562 defines.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "ApprovalCreateDTO",
    "ApprovalDTO",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalResponse",
    "ApprovalsRepository",
    "ApprovalsService",
    "build_approvals_router",
    "get_approvals_service",
]

# Public name -> module that defines it. The order follows the module list.
_EXPORTS: dict[str, str] = {
    "ApprovalDecisionRequest": "src.modules.approvals.data",
    "ApprovalDecisionResponse": "src.modules.approvals.data",
    "ApprovalResponse": "src.modules.approvals.data",
    "get_approvals_service": "src.modules.approvals.dependencies",
    "ApprovalCreateDTO": "src.modules.approvals.repository",
    "ApprovalDTO": "src.modules.approvals.repository",
    "ApprovalsRepository": "src.modules.approvals.repository",
    "build_approvals_router": "src.modules.approvals.router",
    "ApprovalsService": "src.modules.approvals.service",
}


def __getattr__(name: str) -> Any:
    """Import one public name on its first access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List the public names of the package."""
    return sorted(__all__)
