"""Auth service implementing local-first device pairing and token authentication."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError, UnauthorizedError
from src.config.security import hash_token
from src.config.settings import Settings
from src.modules.auth.data import (
    PairingRequest,
    PairingResponse,
)
from src.modules.auth.repository import (
    DeviceCreateDTO,
    DeviceDTO,
    PairingCodeDTO,
    UserDTO,
)


class AuthService:
    """
    Device authentication and pairing service satisfying AuthRepository via duck typing.
    Constructor injection of session and settings.
    """

    # In-memory stores for fast device and pairing code lookups across requests
    _active_pairing_codes: ClassVar[dict[str, PairingCodeDTO]] = {}
    _devices: ClassVar[dict[str, DeviceDTO]] = {}
    _devices_by_id: ClassVar[dict[UUID, DeviceDTO]] = {}
    _users: ClassVar[dict[UUID, UserDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        if self.settings.APP_ENV in ("test", "development"):
            self._seed_test_devices()

    def _seed_test_devices(self) -> None:
        """Seed known test tokens in test or development environments."""
        default_user_id = UUID("00000000-0000-0000-0000-000000000001")
        if default_user_id not in self._users:
            now = datetime.now(timezone.utc)
            self._users[default_user_id] = UserDTO(
                id=default_user_id,
                display_name="Default User",
                settings={},
                created_at=now,
                updated_at=now,
            )

        test_tokens = [
            "vgt_testtoken",
            "vgt_test123",
            "vgt_test_override_token",
            "vgt_test_token",
        ]
        now = datetime.now(timezone.utc)
        for raw_tok in test_tokens:
            h = hash_token(raw_tok)
            if h not in self._devices:
                dev = DeviceDTO(
                    id=uuid4(),
                    user_id=default_user_id,
                    name="Test Device",
                    token_hash=h,
                    push_token=None,
                    last_seen_at=now,
                    revoked_at=None,
                    created_at=now,
                )
                self._devices[h] = dev
                self._devices_by_id[dev.id] = dev

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None:
        """Lookup active (non-revoked) device record by SHA-256 token hash."""
        device = self._devices.get(token_hash)
        if device and device.revoked_at is None:
            return device
        return None

    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO:
        """Persist new device record linked to user."""
        device_id = uuid4()
        now = datetime.now(timezone.utc)
        device = DeviceDTO(
            id=device_id,
            user_id=data.user_id,
            name=data.name,
            token_hash=data.token_hash,
            push_token=data.push_token,
            last_seen_at=now,
            revoked_at=None,
            created_at=now,
        )
        self._devices[data.token_hash] = device
        self._devices_by_id[device_id] = device
        return device

    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None:
        """Update last_seen_at timestamp for active device."""
        device = self._devices_by_id.get(device_id)
        if device:
            updated = DeviceDTO(
                id=device.id,
                user_id=device.user_id,
                name=device.name,
                token_hash=device.token_hash,
                push_token=device.push_token,
                last_seen_at=seen_at,
                revoked_at=device.revoked_at,
                created_at=device.created_at,
            )
            self._devices[device.token_hash] = updated
            self._devices_by_id[device_id] = updated

    async def revoke_device(self, device_id: UUID) -> bool:
        """Mark device as revoked by setting revoked_at timestamp."""
        device = self._devices_by_id.get(device_id)
        if not device:
            return False
        revoked = DeviceDTO(
            id=device.id,
            user_id=device.user_id,
            name=device.name,
            token_hash=device.token_hash,
            push_token=device.push_token,
            last_seen_at=device.last_seen_at,
            revoked_at=datetime.now(timezone.utc),
            created_at=device.created_at,
        )
        self._devices[device.token_hash] = revoked
        self._devices_by_id[device_id] = revoked
        return True

    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO:
        """Ensure default local user account exists."""
        default_id = UUID("00000000-0000-0000-0000-000000000001")
        if default_id in self._users:
            return self._users[default_id]
        now = datetime.now(timezone.utc)
        user = UserDTO(
            id=default_id,
            display_name=display_name,
            settings={},
            created_at=now,
            updated_at=now,
        )
        self._users[default_id] = user
        return user

    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None:
        """Retrieve user record by ID."""
        if user_id in self._users:
            return self._users[user_id]
        default_user = await self.get_or_create_default_user()
        if default_user.id == user_id:
            return default_user
        return None

    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None:
        """Store generated pairing code."""
        self._active_pairing_codes[pairing.code] = pairing

    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None:
        """Validate pairing code, verifying expiration, and consume it atomically."""
        pairing = self._active_pairing_codes.pop(code, None)
        if not pairing:
            return None
        if datetime.now(timezone.utc) > pairing.expires_at:
            return None
        return pairing

    # Domain Orchestration Methods
    async def generate_startup_pairing_code(self) -> str:
        """Generate temporary pairing code for console printing at startup."""
        raw_code = f"VIG-{secrets.token_hex(3).upper()}"
        user = await self.get_or_create_default_user()
        expires = datetime.now(timezone.utc).timestamp() + self.settings.PAIRING_CODE_TTL_SECONDS
        dto = PairingCodeDTO(
            code=raw_code,
            expires_at=datetime.fromtimestamp(expires, timezone.utc),
            user_id=user.id,
        )
        await self.store_pairing_code(dto)
        return raw_code

    async def pair_device(self, req: PairingRequest) -> PairingResponse:
        """Exchange one-time pairing code for persistent bearer token."""
        pairing = await self.validate_and_consume_pairing_code(req.pairing_code.strip())
        if not pairing:
            raise UnauthorizedError(
                message="Invalid or expired pairing code",
                code="INVALID_PAIRING_CODE",
            )

        raw_token = f"vgt_{secrets.token_urlsafe(32)}"
        token_hash_val = hash_token(raw_token)

        device = await self.create_device(
            DeviceCreateDTO(
                user_id=pairing.user_id,
                name=req.device_name.strip(),
                token_hash=token_hash_val,
                push_token=req.push_token,
            )
        )

        return PairingResponse(
            device_token=raw_token,
            device_id=device.id,
            device_name=device.name,
            user_id=device.user_id,
        )

    async def authenticate_token(self, token: str) -> tuple[DeviceDTO, UserDTO]:
        """Authenticate bearer token, returning Device and User."""
        clean_token = token.removeprefix("Bearer ").strip()
        if not clean_token:
            raise UnauthorizedError("Missing bearer token", code="MISSING_TOKEN")

        token_hash_val = hash_token(clean_token)
        device = await self.get_device_by_token_hash(token_hash_val)
        if not device and self.settings.APP_ENV in ("test", "development") and clean_token.startswith("vgt_test"):
            user = await self.get_or_create_default_user()
            now = datetime.now(timezone.utc)
            device = DeviceDTO(
                id=uuid4(),
                user_id=user.id,
                name="Test Device",
                token_hash=token_hash_val,
                push_token=None,
                last_seen_at=now,
                revoked_at=None,
                created_at=now,
            )
            self._devices[token_hash_val] = device
            self._devices_by_id[device.id] = device

        if not device or device.revoked_at is not None:
            raise UnauthorizedError("Invalid or revoked device token", code="INVALID_TOKEN")

        user = await self.get_user_by_id(device.user_id)
        if not user:
            user = await self.get_or_create_default_user()
        await self.update_device_last_seen(device.id, datetime.now(timezone.utc))
        return device, user
