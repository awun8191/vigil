"""Unit tests for structured logging and request_id contextvar propagation."""
import json
import logging
import pytest
from src.config.logging import (
    configure_logging,
    get_logger,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def test_request_id_contextvar_lifecycle():
    """Verify context variable bind, retrieve, and reset behavior."""
    assert get_request_id() is None
    token = set_request_id("test-req-123")
    assert get_request_id() == "test-req-123"
    reset_request_id(token)
    assert get_request_id() is None


def test_json_logging_contains_request_id(capsys):
    """Verify JSON log output contains request_id when contextvar is bound."""
    configure_logging(log_level="INFO", log_format="json")
    logger = get_logger("test.json")
    token = set_request_id("req-json-456")
    logger.info("Test JSON log message", user_id="user_1")
    reset_request_id(token)

    captured = capsys.readouterr().out
    lines = [line for line in captured.strip().split("\n") if line]
    data = json.loads(lines[-1])
    assert data["event"] == "Test JSON log message"
    assert data["request_id"] == "req-json-456"
    assert data["user_id"] == "user_1"
    assert data["level"] == "info"
    assert "timestamp" in data


def test_json_logging_without_request_id(capsys):
    """Verify JSON log output omits request_id when contextvar is unset."""
    configure_logging(log_level="INFO", log_format="json")
    logger = get_logger("test.json.none")
    logger.info("Message without correlation")

    captured = capsys.readouterr().out
    lines = [line for line in captured.strip().split("\n") if line]
    data = json.loads(lines[-1])
    assert "request_id" not in data


def test_console_logging_format(capsys):
    """Verify console renderer format outputs human-readable message and keys."""
    configure_logging(log_level="INFO", log_format="console")
    logger = get_logger("test.console")
    token = set_request_id("req-con-789")
    logger.info("Console message", key="val")
    reset_request_id(token)

    captured = capsys.readouterr().out
    assert "Console message" in captured
    assert "req-con-789" in captured
    assert "key=val" in captured


def test_stdlib_logging_interception(capsys):
    """Verify standard library loggers route through structlog formatter with request_id."""
    configure_logging(log_level="INFO", log_format="json")
    stdlib_logger = logging.getLogger("stdlib.test")
    token = set_request_id("req-stdlib-111")
    stdlib_logger.info("Message from standard library")
    reset_request_id(token)

    captured = capsys.readouterr().out
    lines = [line for line in captured.strip().split("\n") if line]
    data = json.loads(lines[-1])
    assert data["event"] == "Message from standard library"
    assert data["request_id"] == "req-stdlib-111"


def test_exception_logging_renders_traceback(capsys):
    """Verify logger.exception renders traceback into exception attribute."""
    configure_logging(log_level="INFO", log_format="json")
    logger = get_logger("test.exception")
    try:
        raise ValueError("Simulated fault")
    except ValueError:
        logger.exception("Error occurred during computation")

    captured = capsys.readouterr().out
    lines = [line for line in captured.strip().split("\n") if line]
    data = json.loads(lines[-1])
    assert data["event"] == "Error occurred during computation"
    assert "exception" in data
    assert "ValueError: Simulated fault" in data["exception"]
