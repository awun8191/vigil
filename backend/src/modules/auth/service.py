"""Auth service implementing local-first device pairing and token authentication."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import UnauthorizedError
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
from src.modules.auth.state import DEFAULT_USER_ID, InMemoryAuthStore, SqlAuthStore


class AuthService:
    """
    Device authentication and pairing service satisfying AuthRepository via duck typing.

    The store swaps underneath the logic: the SQL implementation on a request
    database session keeps users and devices durable across restarts, the
    in-memory one serves tests and the CLI harness. Pairing codes stay
    process-local in either case, each startup issues fresh ones.
    """

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        if session is not None:
            self._store: InMemoryAuthStore | SqlAuthStore = SqlAuthStore(session)
        else:
            self._store = InMemoryAuthStore()
        # Test devices are never seeded here. Only the test suite calls
        # seed_test_devices(), and that method refuses to run outside APP_ENV=test.

    def seed_test_devices(self) -> None:
        """Seed the deterministic test device tokens into the in-memory store.

        This method exists for the test suite only. It raises unless APP_ENV is
        "test", so a development or production process can never obtain a
        device that authenticates by well-known token. HTTP-level tests reach
        the SQL store instead, so the conftest seeds those rows in the test
        database directly.
        """
        if self.settings.APP_ENV != "test":
            raise RuntimeError(
                "seed_test_devices() requires APP_ENV='test' "
                f"(got {self.settings.APP_ENV!r})"
            )
        from src.modules.auth.state import DEFAULT_USER_ID, InMemoryAuthStore

        if DEFAULT_USER_ID not in InMemoryAuthStore.users:
            now = datetime.now(timezone.utc)
            InMemoryAuthStore.users[DEFAULT_USER_ID] = UserDTO(
                id=DEFAULT_USER_ID,
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
            if h not in InMemoryAuthStore.devices:
                dev = DeviceDTO(
                    id=uuid4(),
                    user_id=DEFAULT_USER_ID,
                    name="Test Device",
                    token_hash=h,
                    push_token=None,
                    last_seen_at=now,
                    revoked_at=None,
                    created_at=now,
                )
                InMemoryAuthStore.devices[h] = dev
                InMemoryAuthStore.devices_by_id[dev.id] = dev

    async def get_device_by_token_hash(self, token_hash: str) -> DeviceDTO | None:
        """Lookup active (non-revoked) device record by SHA-256 token hash."""
        return await self._store.get_device_by_token_hash(token_hash)

    async def create_device(self, data: DeviceCreateDTO) -> DeviceDTO:
        """Persist new device record linked to user."""
        return await self._store.create_device(data)

    async def update_device_last_seen(self, device_id: UUID, seen_at: datetime) -> None:
        """Update last_seen_at timestamp for active device."""
        await self._store.update_device_last_seen(device_id, seen_at)

    async def revoke_device(self, device_id: UUID) -> bool:
        """Mark device as revoked by setting revoked_at timestamp."""
        return await self._store.revoke_device(device_id)

    async def get_or_create_default_user(self, display_name: str = "Default User") -> UserDTO:
        """Ensure default local user account exists."""
        return await self._store.get_or_create_default_user(display_name)

    async def get_user_by_id(self, user_id: UUID) -> UserDTO | None:
        """Retrieve user record by ID."""
        user = await self._store.get_user_by_id(user_id)
        if user is not None:
            return user
        default_user = await self.get_or_create_default_user()
        if default_user.id == user_id:
            return default_user
        return None

    async def store_pairing_code(self, pairing: PairingCodeDTO) -> None:
        """Store generated pairing code."""
        await self._store.store_pairing_code(pairing)

    async def validate_and_consume_pairing_code(self, code: str) -> PairingCodeDTO | None:
        """Validate pairing code, verifying expiration, and consume it atomically."""
        return await self._store.validate_and_consume_pairing_code(code)

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

        # The default user row must exist before the device FK can land; on the
        # SQL store the row appears here on the request session.
        paired_user = pairing.user_id
        if paired_user == DEFAULT_USER_ID:
            await self._store.get_or_create_default_user()

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
        if not device or device.revoked_at is not None:
            raise UnauthorizedError("Invalid or revoked device token", code="INVALID_TOKEN")

        user = await self.get_user_by_id(device.user_id)
        if not user:
            user = await self.get_or_create_default_user()
        await self.update_device_last_seen(device.id, datetime.now(timezone.utc))
        return device, user
