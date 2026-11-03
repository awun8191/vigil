"""Unit tests for the connector stores and the store the service picks."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    MCPServerDTO,
)
from src.modules.connectors.service import ConnectorsService
from src.modules.connectors.state import (
    ConnectorStore,
    InMemoryConnectorStore,
)


def make_service() -> ConnectorsService:
    return ConnectorsService(session=None, settings=Settings(APP_ENV="test"))


def test_sessionless_service_uses_the_in_memory_store() -> None:
    """Without a session the service falls back to the process-local store."""
    service = make_service()
    assert isinstance(service.repository, InMemoryConnectorStore)
    assert isinstance(service.repository, ConnectorStore)


async def test_in_memory_account_round_trip_and_status_update() -> None:
    """An account saved through the store is readable and its status updates."""
    service = make_service()
    user_id = uuid4()
    account = await service.save_account(
        ConnectorAccountCreateDTO(
            user_id=user_id,
            connector_id="gmail",
            account_label=f"unit-{uuid4().hex[:8]}@example.com",
            encrypted_credentials="ciphertext-blob",
            scopes=["https://mail.google.com/"],
        )
    )

    assert await service.get_account(account.id) == account
    assert [row.id for row in await service.list_accounts(user_id)] == [account.id]
    assert await service.get_account(uuid4()) is None

    updated = await service.update_account_status(account.id, "needs_reauth")
    assert updated is not None
    assert updated.status == "needs_reauth"
    assert updated.encrypted_credentials == "ciphertext-blob"
    stored = await service.get_account(account.id)
    assert stored is not None
    assert stored.status == "needs_reauth"


async def test_in_memory_store_registers_and_removes_a_server() -> None:
    """The store keeps one row per name and deletes it on request."""
    store = InMemoryConnectorStore()
    server = MCPServerDTO(
        id=uuid4(),
        name=f"unit-mcp-{uuid4().hex[:8]}",
        transport="sse",
        command=None,
        url="http://127.0.0.1:9/sse",
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )

    await store.register_server(server)
    assert await store.get_server(server.name) == server
    assert server in await store.list_servers()

    assert await store.delete_server(server.name) is True
    assert await store.get_server(server.name) is None
    assert await store.delete_server(server.name) is False


@pytest.mark.asyncio
async def test_mcp_server_cache_is_populated_from_the_store() -> None:
    """The read-through cache mcp.py reads mirrors each stored server."""
    service = make_service()
    store = service.repository
    assert isinstance(store, InMemoryConnectorStore)
    server = MCPServerDTO(
        id=uuid4(),
        name=f"unit-cache-{uuid4().hex[:8]}",
        transport="stdio",
        command="python -m example",
        url=None,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    await store.register_server(server)

    restored = await service.get_mcp_server(server.name)
    assert restored == server
    assert ConnectorsService._mcp_servers_by_name[server.name] == server
    assert ConnectorsService._mcp_servers[server.id] == server

    assert await service.unregister_mcp_server(server.name) is True
    assert server.name not in ConnectorsService._mcp_servers_by_name
    assert server.id not in ConnectorsService._mcp_servers
