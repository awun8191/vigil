"""Domain errors for the connectors module.

They live apart from ``service`` so the store can translate a database fault
into one without importing the service that owns it.
"""
from __future__ import annotations


class MCPRegistrationError(ValueError):
    """A third party MCP server cannot be registered. The message names the reason."""


__all__ = ["MCPRegistrationError"]
