"""Auth stores: durable SQL for users and devices, process-local pairing codes.

The service keeps its logic and the store swaps underneath it: SQL on a
request database session, in-memory dicts in tests and the CLI harness.
Pairing codes are ephemeral by design (a new one prints at every startup), so
they stay in a module-level map for both stores.
"""
from __future__ import annotations

from dataclasses import dataclass, replace as _replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.repository import (
    DeviceCreateDTO,
    DeviceDTO,
    PairingCodeDTO,
    UserDTO,
)
from src.schemas.identity import DeviceModel, UserModel

DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
"""The single local user account every device pairs against."""

PAIRING_CODES: dict[str, PairingCodeDTO] = {}
"""Live one-time pairing codes. Process-local: each startup prints a fresh code."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class AuthStore(Protocol):
    """Storage contract behind AuthService."""

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None: ...
    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO: ...
    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None: ...
    async def revoke_device(self, device_id: UUID) -> bool: ...
    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO: ...
    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None: ...
    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None: ...
    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None: ...


class InMemoryAuthStore:
    """Module-level dict store, identical semantics to the original service dicts."""

    devices: dict[str, DeviceDTO] = {}
    devices_by_id: dict[UUID, DeviceDTO] = {}
    users: dict[UUID, UserDTO] = {}

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None:
        device = InMemoryAuthStore.devices.get(token_hash)
        if device and device.revoked_at is None:
            return device
        return None

    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO:
        now = _now()
        device = DeviceDTO(
            id=uuid4(),
            user_id=data.user_id,
            name=data.name,
            token_hash=data.token_hash,
            push_token=data.push_token,
            last_seen_at=now,
            revoked_at=None,
            created_at=now,
        )
        InMemoryAuthStore.devices[data.token_hash] = device
        InMemoryAuthStore.devices_by_id[device.id] = device
        return device

    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None:
        device = InMemoryAuthStore.devices_by_id.get(device_id)
        if device:
            updated = _replace(device, last_seen_at=seen_at)
            InMemoryAuthStore.devices[device.token_hash] = updated
            InMemoryAuthStore.devices_by_id[device_id] = updated

    async def revoke_device(self, device_id: UUID) -> bool:
        device = InMemoryAuthStore.devices_by_id.get(device_id)
        if not device:
            return False
        revoked = _replace(device, revoked_at=_now())
        InMemoryAuthStore.devices[device.token_hash] = revoked
        InMemoryAuthStore.devices_by_id[device_id] = revoked
        return True

    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO:
        if DEFAULT_USER_ID in InMemoryAuthStore.users:
            return InMemoryAuthStore.users[DEFAULT_USER_ID]
        user = UserDTO(
            id=DEFAULT_USER_ID,
            display_name=display_name,
            settings={},
            created_at=_now(),
            updated_at=_now(),
        )
        InMemoryAuthStore.users[DEFAULT_USER_ID] = user
        return user

    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None:
        if user_id in InMemoryAuthStore.users:
            return InMemoryAuthStore.users[user_id]
        return None

    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None:
        PAIRING_CODES[pairing.code] = pairing

    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None:
        pairing = PAIRING_CODES.pop(code, None)
        if not pairing:
            return None
        if _now() > pairing.expires_at:
            return None
        return pairing


class SqlAuthStore:
    """Durable auth store over the ``users`` and ``devices`` tables.

    A paired device survives a process restart, which is the whole point: the
    web client keeps one bearer token for the lifetime of the database.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _device_dto(model: DeviceModel) -> DeviceDTO:
        return DeviceDTO(
            id=model.id,
            user_id=model.user_id,
            name=model.name,
            token_hash=model.token_hash,
            push_token=model.push_token,
            last_seen_at=model.last_seen_at,
            revoked_at=model.revoked_at,
            created_at=model.created_at,
        )

    @staticmethod
    def _user_dto(model: UserModel) -> UserDTO:
        return UserDTO(
            id=model.id,
            display_name=model.display_name,
            settings=dict(model.settings),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def _ensure_user_row(self, user_id: UUID, display_name: str) -> UserModel:
        model = await self.session.get(UserModel, user_id)
        if model is not None:
            return model
        model = UserModel(id=user_id, display_name=display_name, settings={})
        self.session.add(model)
        await self.session.flush()
        await self.session.commit()
        return model

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None:
        result = await self.session.execute(
            select(DeviceModel).where(DeviceModel.token_hash == token_hash)
        )
        model = result.scalars().first()
        if model is None or model.revoked_at is not None:
            return None
        return self._device_dto(model)

    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO:
        model = DeviceModel(
            id=uuid4(),
            user_id=data.user_id,
            name=data.name,
            token_hash=data.token_hash,
            push_token=data.push_token,
            last_seen_at=_now(),
            revoked_at=None,
            created_at=_now(),
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.commit()
        return self._device_dto(model)

    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None:
        await self.session.execute(
            update(DeviceModel).where(DeviceModel.id == device_id).values(last_seen_at=seen_at)
        )
        await self.session.commit()

    async def revoke_device(self, device_id: UUID) -> bool:
        result = await self.session.execute(
            update(DeviceModel)
            .where(DeviceModel.id == device_id, DeviceModel.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await self.session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO:
        return self._user_dto(await self._ensure_user_row(DEFAULT_USER_ID, display_name))

    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None:
        model = await self.session.get(UserModel, user_id)
        return None if model is None else self._user_dto(model)

    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None:
        PAIRING_CODES[pairing.code] = pairing

    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None:
        pairing = PAIRING_CODES.pop(code, None)
        if not pairing:
            return None
        if _now() > pairing.expires_at:
            return None
        return pairing


__all__ = [
    "DEFAULT_USER_ID",
    "AuthStore",
    "InMemoryAuthStore",
    "SqlAuthStore",
]
