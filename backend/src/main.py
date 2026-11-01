"""Application factory create_app assembling middlewares, exception handlers, and the root api router."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config.exceptions import register_exception_handlers
from src.config.logging import configure_logging, get_logger
from src.config.router import create_api_router
from src.config.settings import Settings, get_settings, is_unsafe_secret_key
from src.middlewares.idempotency import IdempotencyMiddleware
from src.middlewares.request_id import RequestIdMiddleware
from src.middlewares.timing import TimingMiddleware


def assert_safe_startup_config(settings: Settings) -> None:
    """Refuse to boot a production process that keeps a published secret key.

    Settings validation raises first for this case. This guard also covers a
    Settings object that a caller builds around validation.
    """
    if settings.APP_ENV == "production" and is_unsafe_secret_key(settings.SECRET_KEY):
        raise RuntimeError(
            "Refusing to start: SECRET_KEY is not safe for production. "
            "Set SECRET_KEY to a unique value, for example: openssl rand -hex 32"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for database connection pool and resource management."""
    settings: Settings = app.state.settings
    logger = get_logger("virgil.lifespan")
    logger.info("Starting Virgil application", app_env=settings.APP_ENV, port=settings.PORT)

    # Output startup ASCII QR code and device pairing code in development/production
    if settings.APP_ENV != "test":
        try:
            from src.modules.auth.service import AuthService
            auth_service = AuthService(session=None, settings=settings)  # type: ignore[arg-type]
            pairing_code = await auth_service.generate_startup_pairing_code()
            pair_url = f"virgil://pair?code={pairing_code}&server=http://localhost:{settings.PORT}"

            import io
            import qrcode
            qr = qrcode.QRCode()
            qr.add_data(pair_url)
            qr.make()
            buf = io.StringIO()
            qr.print_ascii(out=buf)
            qr_ascii = buf.getvalue()

            logger.info("============================================================")
            logger.info("VIRGIL DEVICE PAIRING READY")
            logger.info(f"Temporary Pairing Code: {pairing_code}")
            logger.info(f"Pairing URL: {pair_url}")
            logger.info(f"\n{qr_ascii}")
            logger.info("Scan QR code or enter pairing code in mobile app to pair.")
            logger.info("============================================================")
        except Exception as exc:
            logger.warning("Failed to render startup pairing QR code", error=str(exc))

    yield
    logger.info("Shutting down Virgil application")


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    FastAPI application factory ensuring strict dependency injection and testing isolation.

    Args:
        settings: Optional Settings instance. If None, loaded via get_settings().

    Returns:
        Configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    # 0. Refuse an unsafe production configuration before any resource is built
    assert_safe_startup_config(settings)

    # 1. Initialize structured logging
    configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)

    # 2. Instantiate FastAPI
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="Virgil: Open-Source Self-Hostable Proactive AI Assistant & Execution Harness",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # 3. Attach Middlewares in LIFO reverse order
    # Starlette wraps middleware in reverse: last added runs first inbound.
    # Inbound order: CORS -> RequestId -> Timing -> Idempotency -> Route Handler

    # Innermost: Idempotency middleware (deduplicates mutating operations)
    app.add_middleware(IdempotencyMiddleware)

    # Middle: Timing middleware (records X-Process-Time latency)
    app.add_middleware(TimingMiddleware)

    # Outer: Request ID middleware (generates/propagates X-Request-ID and binds contextvar)
    app.add_middleware(RequestIdMiddleware)

    # Outermost: CORS middleware (handles preflight OPTIONS requests)
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 4. Attach uniform exception handlers
    register_exception_handlers(app)

    # 5. Mount root API router aggregating all 14 domain modules
    app.include_router(create_api_router(settings))

    return app


# Default application instance for ASGI servers (e.g. uvicorn src.main:app)
app = create_app()
