"""AES-256-GCM authenticated Key Vault service and schemas.

The vault is durable: ``vault_secrets`` holds one ciphertext per (user, key
name). The service keeps two stores so a service built without a session (unit
tests and the CLI harness) still works, while the request path writes rows.

Nothing here logs a plaintext value. Listing decrypts only to mask, and a
record that cannot be decrypted is masked as ``****`` rather than failing the
whole list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, ClassVar, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.security import decrypt_secret, encrypt_secret
from src.config.settings import Settings, get_settings
from src.schemas.vault import VaultSecretModel


class VaultSecretDTO(BaseModel):
    """Wire representation of a stored secret with masked content."""

    model_config = ConfigDict(from_attributes=True)

    key_name: str
    masked_value: str
    created_at: datetime
    updated_at: datetime


class SetSecretRequest(BaseModel):
    """Request payload to store or update a secret in the vault."""

    key: str = Field(..., min_length=1, description="Unique secret name/identifier")
    value: str = Field(..., min_length=1, description="Plaintext secret value to encrypt")


def mask_secret_value(value: str) -> str:
    """Mask a secret value according to the security masking rules."""
    if len(value) > 8:
        return f"{value[:3]}****{value[-4:]}"
    return "****"


def normalize_key_name(key: str) -> str:
    """Return the canonical key name, or raise when it is not usable.

    A key is an environment-variable style identifier. Surrounding whitespace is
    not part of it, and a name longer than the column is rejected here rather
    than truncated by the database.
    """
    key_clean = (key or "").strip()
    if not key_clean:
        raise ValueError("Key name cannot be empty")
    if len(key_clean) > 255:
        raise ValueError("Key name must be 255 characters or fewer")
    return key_clean


def normalize_secret_value(key_name: str, value: str) -> str:
    """Normalize token-shaped secrets without touching passwords.

    Users paste API tokens with a ``Bearer`` prefix or a trailing newline. Both
    would turn into ``Authorization: Bearer Bearer …`` or a malformed header at
    the provider. Password-shaped secrets keep every character, because a
    trailing space can be meaningful there.
    """
    upper = key_name.upper()
    if upper.endswith(("_API_KEY", "_TOKEN", "_PAT", "_ACCESS_TOKEN")):
        token = value.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token
    return value


def _coerce_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StoredVaultRecord:
    """Internal model for an encrypted vault entry."""

    user_id: UUID
    key_name: str
    ciphertext: str
    created_at: datetime
    updated_at: datetime
    id: UUID | None = None


@runtime_checkable
class VaultStore(Protocol):
    """Storage contract behind KeyVaultService."""

    async def get(self, user_id: UUID, key_name: str) -> StoredVaultRecord | None: ...

    async def list(self, user_id: UUID) -> list[StoredVaultRecord]: ...

    async def upsert(self, record: StoredVaultRecord) -> StoredVaultRecord: ...

    async def delete(self, user_id: UUID, key_name: str) -> bool: ...


class InMemoryVaultStore:
    """Process-local store used when a service is built without a session.

    The module keeps one instance for the request fallback, so a save and a
    later list in the same process see the same rows. Tests that want isolation
    pass their own instance through ``store=``.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], StoredVaultRecord] = {}

    async def get(self, user_id: UUID, key_name: str) -> StoredVaultRecord | None:
        return self.rows.get((user_id, key_name))

    async def list(self, user_id: UUID) -> list[StoredVaultRecord]:
        return [
            record
            for (uid, _key), record in self.rows.items()
            if uid == user_id
        ]

    async def upsert(self, record: StoredVaultRecord) -> StoredVaultRecord:
        existing = self.rows.get((record.user_id, record.key_name))
        if existing is not None:
            record.created_at = existing.created_at
        record.updated_at = _now()
        self.rows[(record.user_id, record.key_name)] = record
        return record

    async def delete(self, user_id: UUID, key_name: str) -> bool:
        return self.rows.pop((user_id, key_name), None) is not None


class SqlVaultStore:
    """Durable store over the ``vault_secrets`` table.

    The store flushes and never commits or rolls back. The request session, the
    worker or the test owns the transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _record(model: VaultSecretModel) -> StoredVaultRecord:
        return StoredVaultRecord(
            id=model.id,
            user_id=model.user_id,
            key_name=model.key_name,
            ciphertext=model.ciphertext,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get(self, user_id: UUID, key_name: str) -> StoredVaultRecord | None:
        result = await self.session.execute(
            select(VaultSecretModel).where(
                VaultSecretModel.user_id == user_id,
                VaultSecretModel.key_name == key_name,
            )
        )
        model = result.scalars().first()
        return None if model is None else self._record(model)

    async def list(self, user_id: UUID) -> list[StoredVaultRecord]:
        result = await self.session.execute(
            select(VaultSecretModel)
            .where(VaultSecretModel.user_id == user_id)
            .order_by(VaultSecretModel.key_name.asc())
        )
        return [self._record(model) for model in result.scalars().all()]

    async def upsert(self, record: StoredVaultRecord) -> StoredVaultRecord:
        """Insert the secret, or replace the ciphertext of the same key name."""
        now = record.updated_at or _now()
        statement = (
            pg_insert(VaultSecretModel)
            .values(
                id=record.id or uuid4(),
                user_id=record.user_id,
                key_name=record.key_name,
                ciphertext=record.ciphertext,
                created_at=record.created_at or now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_vault_secrets_user_key",
                set_={"ciphertext": record.ciphertext, "updated_at": now},
            )
            .returning(VaultSecretModel.id)
        )
        secret_id = (await self.session.execute(statement)).scalar_one()
        await self.session.flush()
        model = await self.session.get(VaultSecretModel, secret_id)
        if model is None:
            raise RuntimeError(
                f"vault secret {secret_id} is missing after a successful upsert"
            )
        return self._record(model)

    async def delete(self, user_id: UUID, key_name: str) -> bool:
        result = await self.session.execute(
            sa_delete(VaultSecretModel)
            .where(
                VaultSecretModel.user_id == user_id,
                VaultSecretModel.key_name == key_name,
            )
            .returning(VaultSecretModel.id)
        )
        return result.scalar_one_or_none() is not None


_FALLBACK_STORE = InMemoryVaultStore()


class KeyVaultService:
    """
    AES-256-GCM authenticated Key Vault service.

    Encrypts every secret with ``settings.SECRET_KEY`` before it reaches a
    store. Plaintext is never returned by a listing, and the store only ever
    sees ciphertext.
    """

    _fallback_store: ClassVar[InMemoryVaultStore] = _FALLBACK_STORE

    def __init__(
        self,
        session: AsyncSession | None = None,
        settings: Settings | None = None,
        store: VaultStore | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        if store is not None:
            self._store = store
        elif session is not None:
            self._store = SqlVaultStore(session)
        else:
            self._store = _FALLBACK_STORE
        self._memory_store = (
            self._store if isinstance(self._store, InMemoryVaultStore) else None
        )

    # --- async path (requests, worker) -----------------------------------

    async def set_secret(self, user_id: UUID | str, key: str, value: str) -> None:
        """Encrypt and store secret."""
        key_clean = normalize_key_name(key)
        ciphertext = encrypt_secret(
            normalize_secret_value(key_clean, value), self.settings.SECRET_KEY
        )
        now = _now()
        await self._store.upsert(
            StoredVaultRecord(
                user_id=_coerce_uuid(user_id),
                key_name=key_clean,
                ciphertext=ciphertext,
                created_at=now,
                updated_at=now,
            )
        )

    async def get_secret(
        self, user_id: UUID | str, key: str
    ) -> str | None:
        """Retrieve and decrypt one secret owned by ``user_id``."""
        record = await self._store.get(_coerce_uuid(user_id), normalize_key_name(key))
        if record is None:
            return None
        return decrypt_secret(record.ciphertext, self.settings.SECRET_KEY)

    async def delete_secret(self, user_id: UUID | str, key: str) -> bool:
        """Delete a secret for a user."""
        return await self._store.delete(_coerce_uuid(user_id), normalize_key_name(key))

    async def list_secrets(self, user_id: UUID | str) -> list[VaultSecretDTO]:
        """List secrets for a user with masked values. Never returns plaintext."""
        records = await self._store.list(_coerce_uuid(user_id))
        records.sort(key=lambda record: record.key_name)
        return self._to_dtos(records)

    # --- sync path (tests and the CLI harness) ----------------------------
    #
    # Only an in-memory store can answer synchronously. A service bound to a
    # database session must use the async methods.

    def _require_memory_store(self) -> InMemoryVaultStore:
        if self._memory_store is None:
            raise RuntimeError(
                "synchronous vault access requires an in-memory store; "
                "use the async methods with a database session"
            )
        return self._memory_store

    def set_secret_sync(self, user_id: UUID | str, key: str, value: str) -> None:
        """Synchronously encrypt and store a secret in the in-memory store."""
        store = self._require_memory_store()
        key_clean = normalize_key_name(key)
        ciphertext = encrypt_secret(
            normalize_secret_value(key_clean, value), self.settings.SECRET_KEY
        )
        now = _now()
        storage_key = (_coerce_uuid(user_id), key_clean)
        existing = store.rows.get(storage_key)
        store.rows[storage_key] = StoredVaultRecord(
            user_id=storage_key[0],
            key_name=key_clean,
            ciphertext=ciphertext,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def get_secret_sync(self, user_id: UUID | str, key: str) -> str | None:
        """Synchronously retrieve and decrypt one secret."""
        store = self._require_memory_store()
        record = store.rows.get((_coerce_uuid(user_id), normalize_key_name(key)))
        if record is None:
            return None
        return decrypt_secret(record.ciphertext, self.settings.SECRET_KEY)

    def delete_secret_sync(self, user_id: UUID | str, key: str) -> bool:
        """Synchronously delete a secret."""
        store = self._require_memory_store()
        return store.rows.pop((_coerce_uuid(user_id), normalize_key_name(key)), None) is not None

    def list_secrets_sync(self, user_id: UUID | str) -> list[VaultSecretDTO]:
        """Synchronously list masked secrets."""
        store = self._require_memory_store()
        uid = _coerce_uuid(user_id)
        records = [
            record
            for (owner, _key), record in store.rows.items()
            if owner == uid
        ]
        records.sort(key=lambda record: record.key_name)
        return self._to_dtos(records)

    # --- shared ----------------------------------------------------------

    def _to_dtos(self, records: list[StoredVaultRecord]) -> list[VaultSecretDTO]:
        results: list[VaultSecretDTO] = []
        for record in records:
            try:
                decrypted = decrypt_secret(record.ciphertext, self.settings.SECRET_KEY)
                masked = mask_secret_value(decrypted)
            except Exception:
                masked = "****"
            results.append(
                VaultSecretDTO(
                    key_name=record.key_name,
                    masked_value=masked,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        return results

    @classmethod
    def clear_all(cls) -> None:
        """Testing utility to reset the process-local fallback store."""
        cls._fallback_store.rows.clear()
