"""Connectors repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class ConnectorAccountDTO:
    """Represents a linked external service account (e.g. Gmail)."""
    id: UUID
    user_id: UUID
    connector_id: str  # e.g. "gmail"
    account_label: str  # e.g. "work@example.com"
    encrypted_credentials: str  # AES-256-GCM encrypted
    scopes: list[str]
    status: str  # active, needs_reauth, error, disabled
    enabled_tools: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConnectorAccountCreateDTO:
    """Payload for linking a new connector account."""
    user_id: UUID
    connector_id: str
    account_label: str
    encrypted_credentials: str
    scopes: list[str]
    status: str = "active"
    enabled_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncStateDTO:
    """Represents delta sync tracking cursors for an account."""
    connector_account_id: UUID
    cursor: str  # e.g. Gmail historyId
    last_success_at: datetime
    last_error: str | None = None


@dataclass(frozen=True)
class ItemDTO:
    """Represents an ingested message or event from an external connector."""
    id: UUID
    connector_account_id: UUID
    external_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    body_text: str
    received_at: datetime
    labels: list[str]
    raw_reference: str | None
    created_at: datetime


@dataclass(frozen=True)
class ItemCreateDTO:
    """Payload for ingesting an external item."""
    connector_account_id: UUID
    external_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    body_text: str
    received_at: datetime
    labels: list[str] = field(default_factory=list)
    raw_reference: str | None = None


@dataclass(frozen=True)
class MCPServerDTO:
    """A registered third party MCP server."""

    id: UUID
    name: str
    transport: str  # "stdio" or "sse"
    command: str | None
    url: str | None
    enabled: bool
    created_at: datetime


@runtime_checkable
class ConnectorsRepository(Protocol):
    """Protocol contract for connector accounts, sync cursors, and ingested items."""

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None:
        """
        Retrieve a connector account by ID.

        Args:
            account_id: UUID of the account.

        Returns:
            ConnectorAccountDTO if found, None otherwise.
        """
        ...

    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]:
        """
        List all connector accounts for a user.

        Args:
            user_id: Owner user UUID.

        Returns:
            List of ConnectorAccountDTOs.
        """
        ...

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        """
        Persist a new connector account with encrypted credentials.

        Args:
            data: Account creation DTO.

        Returns:
            The created ConnectorAccountDTO.
        """
        ...

    async def update_account_status(
        self,
        account_id: UUID,
        status: str,
    ) -> ConnectorAccountDTO | None:
        """
        Update the operational status of a connector account.

        Args:
            account_id: UUID of the account.
            status: New status string (active, needs_reauth, error, disabled).

        Returns:
            Updated ConnectorAccountDTO, or None if not found.
        """
        ...

    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None:
        """
        Retrieve delta sync cursor for a connector account.

        Args:
            account_id: UUID of the connector account.

        Returns:
            SyncStateDTO if exists, None otherwise.
        """
        ...

    async def save_sync_state(self, state: SyncStateDTO) -> None:
        """
        Upsert delta sync cursor state for an account.

        Args:
            state: Sync state DTO.
        """
        ...

    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]:
        """
        Batch ingest external items, ignoring duplicates on (connector_account_id, external_id).

        Args:
            items: List of item creation DTOs.

        Returns:
            List of ingested ItemDTOs.
        """
        ...

    async def get_item_by_external_id(
        self,
        account_id: UUID,
        external_id: str,
    ) -> ItemDTO | None:
        """
        Find an ingested item by external service identifier.

        Args:
            account_id: Connector account UUID.
            external_id: Remote message/item identifier.

        Returns:
            ItemDTO if found, None otherwise.
        """
        ...

    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None:
        """
        Retrieve an item by its internal primary key.

        Args:
            item_id: UUID of the item.

        Returns:
            ItemDTO if found, None otherwise.
        """
        ...
