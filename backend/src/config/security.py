"""Security and cryptographic primitives: SHA-256 token hashing and AES-256-GCM encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.exceptions import AppException


class SecurityError(AppException):
    """Raised when encryption, decryption, or cryptographic validation fails."""

    def __init__(
        self,
        message: str = "Security operation failed",
        code: str = "SECURITY_ERROR",
        status_code: int = 500,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=status_code, details=details)


def hash_token(token: str) -> str:
    """
    Compute deterministic SHA-256 hex digest of a token for database indexing.

    Args:
        token: Plaintext token string (e.g. device bearer token).

    Returns:
        64-character lowercase hexadecimal SHA-256 string.
    """
    if not isinstance(token, str):
        raise TypeError("Token must be a string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest().lower()


def verify_token(token: str, token_hash: str) -> bool:
    """
    Constant-time verification of candidate token against stored SHA-256 hash.
    Protects against side-channel timing attacks.

    Args:
        token: Plaintext candidate token.
        token_hash: Expected SHA-256 hex digest.

    Returns:
        True if token matches hash, False otherwise.
    """
    if not isinstance(token, str) or not isinstance(token_hash, str):
        return False
    computed = hash_token(token)
    return hmac.compare_digest(computed, token_hash.lower())


def _normalize_key(secret_key: str | bytes) -> bytes:
    """
    Normalize any secret key to exactly 32 bytes (256 bits) for AES-256-GCM.

    - If 64-char hex string, decode hex.
    - If 32-byte string or bytes, use directly.
    - Otherwise, derive 32 bytes via SHA-256.
    """
    if isinstance(secret_key, str):
        if len(secret_key) == 64:
            try:
                return bytes.fromhex(secret_key)
            except ValueError:
                pass
        raw = secret_key.encode("utf-8")
        if len(raw) == 32:
            return raw
        return hashlib.sha256(raw).digest()
    elif isinstance(secret_key, bytes):
        if len(secret_key) == 32:
            return secret_key
        return hashlib.sha256(secret_key).digest()
    raise TypeError("secret_key must be str or bytes")


def encrypt_secret(plaintext: str, secret_key: str | bytes) -> str:
    """
    Encrypt plaintext using AES-256-GCM authenticated encryption.

    Payload layout: [12-byte Nonce] + [Ciphertext] + [16-byte Tag]
    Returns URL-safe Base64 encoded string.
    """
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be a string")
    key_bytes = _normalize_key(secret_key)
    aesgcm = AESGCM(key_bytes)
    nonce = os.urandom(12)  # 96-bit standard nonce
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    payload = nonce + ciphertext_with_tag
    return base64.urlsafe_b64encode(payload).decode("utf-8")


def decrypt_secret(ciphertext: str, secret_key: str | bytes) -> str:
    """
    Decrypt AES-256-GCM ciphertext from URL-safe Base64 string.

    Raises:
        SecurityError: If payload is invalid, corrupted, or tag verification fails.
    """
    if not isinstance(ciphertext, str):
        raise TypeError("ciphertext must be a string")
    try:
        payload = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
    except Exception as exc:
        raise SecurityError(message="Corrupted base64 ciphertext payload", details=str(exc)) from exc

    if len(payload) < 28:  # 12 bytes nonce + 16 bytes tag minimum
        raise SecurityError(message="Ciphertext payload too short for AES-GCM (minimum 28 bytes)")

    nonce = payload[:12]
    ciphertext_with_tag = payload[12:]
    key_bytes = _normalize_key(secret_key)
    aesgcm = AESGCM(key_bytes)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, associated_data=None)
        return decrypted_bytes.decode("utf-8")
    except InvalidTag as exc:
        raise SecurityError(message="Authentication tag mismatch: data modified or incorrect key") from exc
    except Exception as exc:
        raise SecurityError(message="Decryption failed", details=str(exc)) from exc
