"""Integration tests proving connector state outlives a database session.

Two ``ConnectorsService`` instances run on two separate ``AsyncSession`` objects
in sequence. The first registers an account and an MCP server, the second reads
both back from Postgres. The process-local MCP cache is cleared in between, so
the second read cannot be served by the cache and must come from the rows.

The tests use the configured test database. Every row they create is deleted
before they return. When Postgres is unreachable, or the connector tables are
missing, they skip with a reason.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings
from src.modules.connectors.data import RegisterConnectorRequest
from src.modules.connectors.service import ConnectorsService
from src.schemas.connectors import ConnectedAccountModel
from tests.unit.test_mcp_registration import write_fake_server


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory, or skip when the database cannot serve the test."""
    engine = create_async_engine(Settings().DATABASE_URL)
    try:
        async with engine.connect() as connection:
            for table in ("connected_accounts", "mcp_servers", "users"):
                present = await connection.scalar(
                    text(f"select to_regclass('public.{table}')")
                )
                if present is None:
                    pytest.skip(
                        f"table {table!r} is missing from the test database; "
                        "run the migrations first"
                    )
    except Exception as exc:  # pragma: no cover - depends on the host
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")
    try:
        yield async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_and_mcp_server_survive_a_new_session(
    tmp_path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A new session reads back the account and the MCP server the first wrote."""
    settings = Settings(APP_ENV="test")
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    account_label = f"durable-{suffix}@example.com"
    server_name = f"durable-mcp-{suffix}"
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    raw_credentials = {
        "client_id": "client_durable_test",
        "refresh_token": f"refresh-token-{suffix}",
    }
    command = write_fake_server(
        tmp_path,
        [
            {
                "name": "durable_search",
                "description": "Search durable test data.",
                "input_schema": {"type": "object"},
            }
        ],
    )
    account_id: uuid.UUID | None = None

    try:
        async with session_factory() as setup:
            await setup.execute(
                text(
                    "insert into users (id, display_name) values (:id, :name) "
                    "on conflict (id) do nothing"
                ),
                {"id": user_id, "name": f"durable-connector-{suffix}"},
            )
            await setup.commit()
            accounts_before = await setup.scalar(
                select(func.count()).select_from(ConnectedAccountModel)
            )

        # First session: register through the service, then close it.
        async with session_factory() as first:
            first_service = ConnectorsService(session=first, settings=settings)
            account = await first_service.register_account(
                user_id,
                RegisterConnectorRequest(
                    connector_id="gmail",
                    account_label=account_label,
                    raw_credentials=raw_credentials,
                    scopes=scopes,
                ),
            )
            server = await first_service.register_mcp_server(
                name=server_name, transport="stdio", command=command
            )
            account_id = account.id

        # A restart loses the process-local cache. Only the rows can answer now.
        ConnectorsService.reset_mcp_cache()

        # Second session: read both back with a freshly built service.
        async with session_factory() as second:
            second_service = ConnectorsService(session=second, settings=settings)
            restored_account = await second_service.get_account(account.id)
            restored_server = await second_service.get_mcp_server(server_name)
            listed_accounts = await second_service.list_accounts(user_id)
            listed_servers = await second_service.list_mcp_servers()
            accounts_after = await second.scalar(
                select(func.count()).select_from(ConnectedAccountModel)
            )
            stored_blob = await second.scalar(
                select(ConnectedAccountModel.encrypted_credentials).where(
                    ConnectedAccountModel.id == account.id
                )
            )

        assert restored_account is not None
        assert restored_account.id == account.id
        assert restored_account.user_id == user_id
        assert restored_account.connector_id == "gmail"
        assert restored_account.account_label == account_label
        assert restored_account.scopes == scopes
        assert restored_account.status == "active"
        assert restored_account.encrypted_credentials == account.encrypted_credentials
        assert [row.id for row in listed_accounts] == [account.id]

        assert restored_server is not None
        assert restored_server.id == server.id
        assert restored_server.name == server_name
        assert restored_server.transport == "stdio"
        assert restored_server.command == command
        assert restored_server.url is None
        assert restored_server.enabled is True
        assert server_name in [row.name for row in listed_servers]

        # One account row appeared, and the blob round-trips byte for byte.
        assert accounts_after == accounts_before + 1
        assert stored_blob == account.encrypted_credentials
        assert raw_credentials["refresh_token"] not in stored_blob

        # The cache mcp.py reads is repopulated from the row.
        assert ConnectorsService._mcp_servers_by_name[server_name].id == server.id
    finally:
        async with session_factory() as cleanup:
            if account_id is not None:
                await cleanup.execute(
                    text("delete from connected_accounts where id = :id"),
                    {"id": account_id},
                )
            await cleanup.execute(
                text("delete from mcp_servers where name = :name"),
                {"name": server_name},
            )
            await cleanup.execute(
                text("delete from users where id = :id"), {"id": user_id}
            )
            await cleanup.commit()
