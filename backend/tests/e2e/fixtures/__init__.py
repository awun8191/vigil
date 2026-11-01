"""Fixtures for the Phase 4 end-to-end test.

The e2e test uses a scripted model client and a fake connector. A script in
this directory is the command that the model asks the sandbox to run.
"""
from __future__ import annotations

from tests.e2e.fixtures.fake_connector import FakeConnector
from tests.e2e.fixtures.fake_model import (
    EXTERNAL_ARGUMENTS,
    EXTERNAL_TOOL,
    REPORT_NAME,
    REPORT_TEXT,
    FakeModel,
    write_report_command,
)

__all__ = [
    "EXTERNAL_ARGUMENTS",
    "EXTERNAL_TOOL",
    "REPORT_NAME",
    "REPORT_TEXT",
    "FakeConnector",
    "FakeModel",
    "write_report_command",
]
