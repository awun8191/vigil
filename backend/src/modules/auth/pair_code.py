"""CLI helper to generate a fresh device pairing code from a running Vigil environment.

Usage:
    python -m src.modules.auth.pair_code
    docker compose -f deploy/docker-compose.yml exec api python -m src.modules.auth.pair_code
"""
from __future__ import annotations

import asyncio

from src.config.settings import get_settings
from src.dependencies import _get_sessionmaker
from src.modules.auth.service import AuthService


async def main() -> None:
    settings = get_settings()
    session_factory = _get_sessionmaker(settings)
    async with session_factory() as session:
        auth_service = AuthService(session=session, settings=settings)
        pairing_code = await auth_service.generate_startup_pairing_code()
        await session.commit()

    pair_url = f"vigil://pair?code={pairing_code}&server=http://localhost:{settings.PORT}"
    print("============================================================")
    print("VIGIL DEVICE PAIRING CODE")
    print(f"Temporary Pairing Code: {pairing_code}")
    print(f"Pairing URL: {pair_url}")
    print(f"Expires in {settings.PAIRING_CODE_TTL_SECONDS} seconds.")
    print("============================================================")


if __name__ == "__main__":
    asyncio.run(main())
