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

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings
from src.modules.connectors.data import RegisterConnectorRequest
from src.modules.connectors.repository import ConnectorAccountCreateDTO, MCPServerDTO
from src.modules.connectors.service import ConnectorsService, MCPRegistrationError
from src.schemas.connectors import ConnectedAccountModel, MCPServerModel
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
            # The store flushes and the boundary commits; this test is its own
            # boundary, so closing the session without this would discard both.
            await first.commit()

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
            # Direct selects, with no service and no cache in the path.
            account_row_ids = list(
                await second.scalars(
                    select(ConnectedAccountModel.id).where(
                        ConnectedAccountModel.user_id == user_id,
                        ConnectedAccountModel.connector_id == "gmail",
                        ConnectedAccountModel.account_label == account_label,
                    )
                )
            )
            server_row_names = list(
                await second.scalars(
                    select(MCPServerModel.name).where(MCPServerModel.name == server_name)
                )
            )

        # The rows exist. This is asserted before any account field, because the
        # account half can be served by the process-local store while the row is
        # missing; only a select proves the database has it.
        assert server_row_names == [server_name]
        assert account_row_ids == [account.id]

        # The restored server comes back through the service, not the cache that
        # the restart just emptied.
        assert restored_server is not None
        assert restored_server.id == server.id
        assert restored_server.name == server_name
        assert restored_server.transport == "stdio"
        assert restored_server.command == command
        assert restored_server.url is None
        assert restored_server.enabled is True
        assert server_name in [row.name for row in listed_servers]

        assert restored_account is not None
        assert restored_account.id == account.id
        assert restored_account.user_id == user_id
        assert restored_account.connector_id == "gmail"
        assert restored_account.account_label == account_label
        assert restored_account.scopes == scopes
        assert restored_account.status == "active"
        assert restored_account.encrypted_credentials == account.encrypted_credentials
        assert [row.id for row in listed_accounts] == [account.id]

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


@pytest.mark.asyncio
async def test_saving_the_same_account_twice_replaces_that_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A reconnect rewrites one row: same id, new blob and scopes, later timestamp.

    The unique key on (user_id, connector_id, account_label) is what makes a
    second connect the same account. The insert's update branch has to keep the
    id and created_at, replace the credentials and scopes, and leave exactly one
    row behind.
    """
    settings = Settings(APP_ENV="test")
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    account_label = f"reconnect-{suffix}@example.com"
    first_blob = f"ciphertext-first-{suffix}"
    second_blob = f"ciphertext-second-{suffix}"
    first_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    second_scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    account_id: uuid.UUID | None = None

    try:
        async with session_factory() as setup:
            await setup.execute(
                text(
                    "insert into users (id, display_name) values (:id, :name) "
                    "on conflict (id) do nothing"
                ),
                {"id": user_id, "name": f"reconnect-connector-{suffix}"},
            )
            await setup.commit()

        async with session_factory() as first:
            first_service = ConnectorsService(session=first, settings=settings)
            original = await first_service.save_account(
                ConnectorAccountCreateDTO(
                    user_id=user_id,
                    connector_id="gmail",
                    account_label=account_label,
                    encrypted_credentials=first_blob,
                    scopes=first_scopes,
                )
            )
            account_id = original.id
            # The store flushes, so the test commits its own unit of work.
            await first.commit()

        # Long enough that the second write stamps a strictly later updated_at.
        await asyncio.sleep(0.01)

        # A reconnect after a restart: new session, new service, same triple.
        async with session_factory() as second:
            second_service = ConnectorsService(session=second, settings=settings)
            replaced = await second_service.save_account(
                ConnectorAccountCreateDTO(
                    user_id=user_id,
                    connector_id="gmail",
                    account_label=account_label,
                    encrypted_credentials=second_blob,
                    scopes=second_scopes,
                )
            )
            await second.commit()
            row_count = await second.scalar(
                select(func.count())
                .select_from(ConnectedAccountModel)
                .where(
                    ConnectedAccountModel.user_id == user_id,
                    ConnectedAccountModel.connector_id == "gmail",
                    ConnectedAccountModel.account_label == account_label,
                )
            )
            stored = await second.scalar(
                select(ConnectedAccountModel).where(
                    ConnectedAccountModel.id == original.id
                )
            )

        assert row_count == 1
        assert stored is not None
        assert replaced.id == original.id
        assert stored.id == original.id
        # The update branch replaced the mutable columns ...
        assert stored.encrypted_credentials == second_blob
        assert stored.scopes == second_scopes
        # ... and kept created_at while moving updated_at forward.
        assert stored.created_at == original.created_at
        assert stored.updated_at > original.updated_at
        assert stored.updated_at > stored.created_at
    finally:
        async with session_factory() as cleanup:
            if account_id is not None:
                await cleanup.execute(
                    text("delete from connected_accounts where id = :id"),
                    {"id": account_id},
                )
            await cleanup.execute(
                text("delete from users where id = :id"), {"id": user_id}
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_second_registration_of_one_name_is_a_domain_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A lost race on the unique name is an MCPRegistrationError, not a 500.

    Two workers can both pass the service's "is it registered" pre-check. The
    unique index on ``mcp_servers.name`` rejects the loser, and the flush turns
    that IntegrityError into the same domain error the pre-check raises. The
    store does not roll back: the boundary that owns the transaction does, and
    the committed first row survives the loser's aborted transaction.
    """
    name = f"dup-mcp-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    def candidate() -> MCPServerDTO:
        return MCPServerDTO(
            id=uuid.uuid4(),
            name=name,
            transport="sse",
            command=None,
            url="http://127.0.0.1:9/sse",
            enabled=True,
            created_at=now,
        )

    try:
        async with session_factory() as session:
            service = ConnectorsService(
                session=session, settings=Settings(APP_ENV="test")
            )
            stored = await service.repository.register_server(candidate())
            # The boundary commits the winner before the race is lost.
            await session.commit()

        async with session_factory() as loser:
            loser_service = ConnectorsService(
                session=loser, settings=Settings(APP_ENV="test")
            )
            with pytest.raises(MCPRegistrationError, match="already registered"):
                await loser_service.repository.register_server(candidate())
            # The store leaves the aborted transaction to the boundary.
            await loser.rollback()

        # The winner's row is still there, read through a session that never
        # touched the losing transaction.
        async with session_factory() as reader:
            reader_service = ConnectorsService(
                session=reader, settings=Settings(APP_ENV="test")
            )
            still_stored = await reader_service.repository.get_server(name)
            assert still_stored is not None
            assert still_stored.id == stored.id
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text("delete from mcp_servers where name = :name"), {"name": name}
            )
            await cleanup.commit()
