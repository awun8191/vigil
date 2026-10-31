"""Auth repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class UserDTO:
    """Represents an application user."""
    id: UUID
    display_name: str
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DeviceDTO:
    """Represents a paired client device."""
    id: UUID
    user_id: UUID
    name: str
    token_hash: str
    push_token: str | None
    last_seen_at: datetime
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class DeviceCreateDTO:
    """Payload for registering a newly paired device."""
    user_id: UUID
    name: str
    token_hash: str
    push_token: str | None = None


@dataclass(frozen=True)
class PairingCodeDTO:
    """Represents an active or consumed temporary pairing code."""
    code: str
    expires_at: datetime
    user_id: UUID


@runtime_checkable
class AuthRepository(Protocol):
    """Protocol contract for authentication, device pairing, and user identity."""

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None:
        """
        Retrieve a device by its SHA-256 token hash.

        Args:
            token_hash: Hexadecimal digest of the device bearer token.

        Returns:
            DeviceDTO if found, None if no matching device exists.
        """
        ...

    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO:
        """
        Persist a newly paired device.

        Args:
            data: Device creation DTO.

        Returns:
            The created DeviceDTO.
        """
        ...

    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None:
        """
        Update the last_seen_at timestamp for an authenticated device.

        Args:
            device_id: UUID of the device.
            seen_at: Timestamp of current activity.
        """
        ...

    async def revoke_device(self, device_id: UUID) -> bool:
        """
        Mark a device as revoked, preventing future authentication.

        Args:
            device_id: UUID of the device to revoke.

        Returns:
            True if revoked successfully, False if device not found.
        """
        ...

    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO:
        """
        Retrieve the primary user or create the initial user on first boot.

        Args:
            display_name: Fallback display name for user initialization.

        Returns:
            UserDTO for the default system user.
        """
        ...

    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None:
        """
        Retrieve a user record by its primary key.

        Args:
            user_id: UUID of the user.

        Returns:
            UserDTO if found, None otherwise.
        """
        ...

    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None:
        """
        Record a newly generated temporary pairing code.

        Args:
            pairing: Pairing code DTO with expiration.
        """
        ...

    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None:
        """
        Validate and atomically consume a temporary pairing code.

        Args:
            code: The user-submitted pairing code string.

        Returns:
            PairingCodeDTO if valid and not expired, None if invalid or already consumed.
        """
        ...
