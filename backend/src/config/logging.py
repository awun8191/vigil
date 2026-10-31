"""Structured logging configuration and correlation ID context propagation via structlog."""
from contextvars import ContextVar, Token
import logging
import sys
from typing import Any, Literal
import structlog

# Context variable storing the active request's correlation ID
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id_ctx", default=None)


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind correlation ID to context variable for the active task/coroutine."""
    return _request_id_ctx.set(request_id)


def get_request_id() -> str | None:
    """Retrieve the bound correlation ID if present in active context."""
    return _request_id_ctx.get()


def reset_request_id(token: Token[str | None]) -> None:
    """Reset context variable after request lifecycle completes."""
    _request_id_ctx.reset(token)


def add_request_id_processor(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor injecting active request_id into every log record."""
    req_id = _request_id_ctx.get()
    if req_id is not None and "request_id" not in event_dict:
        event_dict["request_id"] = req_id
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    log_format: Literal["json", "console"] = "json",
) -> None:
    """
    Configure unified structlog and standard library logging pipeline.
    Routes stdlib loggers through ProcessorFormatter for consistent JSON/console output.
    """
    renderer: structlog.types.Processor
    if log_format == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_request_id_processor,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Damping chatty third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def get_logger(name: str = "virgil") -> structlog.stdlib.BoundLogger:
    """Obtain a structured logger instance."""
    return structlog.get_logger(name)
