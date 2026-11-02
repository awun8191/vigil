"""Unit tests for security and cryptographic primitives."""
import base64
import os
import pytest
from src.config.security import (
    SecurityError,
    _normalize_key,
    decrypt_secret,
    encrypt_secret,
    hash_token,
    verify_token,
)


class TestTokenHashing:
    def test_hash_token_deterministic(self) -> None:
        token = "vgt_0123456789abcdef"
        hash1 = hash_token(token)
        hash2 = hash_token(token)
        assert hash1 == hash2
        assert len(hash1) == 64
        assert hash1.islower()

    def test_hash_token_empty_string(self) -> None:
        assert hash_token("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_hash_token_unicode(self) -> None:
        token = "vigil-🔑-secure-token-東京"
        digest = hash_token(token)
        assert len(digest) == 64

    def test_hash_token_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            hash_token(12345)  # type: ignore[arg-type]


class TestTokenVerification:
    def test_verify_token_valid(self) -> None:
        token = "vgt_secret_token_123"
        digest = hash_token(token)
        assert verify_token(token, digest) is True

    def test_verify_token_invalid_mismatch(self) -> None:
        token = "vgt_secret_token_123"
        digest = hash_token(token)
        assert verify_token("vgt_wrong_token_456", digest) is False

    def test_verify_token_case_insensitivity(self) -> None:
        token = "vgt_secret_token_123"
        digest = hash_token(token).upper()
        assert verify_token(token, digest) is True

    def test_verify_token_invalid_types_return_false(self) -> None:
        assert verify_token(None, "abc") is False  # type: ignore[arg-type]
        assert verify_token("abc", None) is False  # type: ignore[arg-type]
        assert verify_token(123, 456) is False  # type: ignore[arg-type]


class TestKeyNormalization:
    def test_normalize_64_char_hex_key(self) -> None:
        hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        key_bytes = _normalize_key(hex_key)
        assert len(key_bytes) == 32
        assert key_bytes == bytes.fromhex(hex_key)

    def test_normalize_32_byte_string(self) -> None:
        raw_str = "12345678901234567890123456789012"
        key_bytes = _normalize_key(raw_str)
        assert len(key_bytes) == 32
        assert key_bytes == raw_str.encode("utf-8")

    def test_normalize_arbitrary_passphrase(self) -> None:
        passphrase = "my-short-passphrase"
        key_bytes = _normalize_key(passphrase)
        assert len(key_bytes) == 32

    def test_normalize_raw_bytes(self) -> None:
        raw = os.urandom(32)
        assert _normalize_key(raw) == raw


class TestSecretEncryptionDecryption:
    KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    def test_encrypt_decrypt_roundtrip(self) -> None:
        plaintext = "super_secret_oauth_refresh_token_xyz"
        ciphertext = encrypt_secret(plaintext, self.KEY)
        assert isinstance(ciphertext, str)
        assert ciphertext != plaintext

        decrypted = decrypt_secret(ciphertext, self.KEY)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self) -> None:
        ciphertext = encrypt_secret("", self.KEY)
        decrypted = decrypt_secret(ciphertext, self.KEY)
        assert decrypted == ""

    def test_encrypt_nonce_randomness(self) -> None:
        plaintext = "constant_secret"
        ct1 = encrypt_secret(plaintext, self.KEY)
        ct2 = encrypt_secret(plaintext, self.KEY)
        assert ct1 != ct2  # Fresh 12-byte random nonce per encryption

    def test_encrypt_unicode_and_json(self) -> None:
        complex_text = '{"token": "🔑-secret-α-β-γ", "roles": ["admin"]}'
        ciphertext = encrypt_secret(complex_text, self.KEY)
        assert decrypt_secret(ciphertext, self.KEY) == complex_text

    def test_decrypt_with_wrong_key_raises_security_error(self) -> None:
        wrong_key = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
        ciphertext = encrypt_secret("sensitive data", self.KEY)
        with pytest.raises(SecurityError) as exc_info:
            decrypt_secret(ciphertext, wrong_key)
        assert "Authentication tag mismatch" in exc_info.value.message

    def test_decrypt_tampered_ciphertext_raises_security_error(self) -> None:
        ciphertext = encrypt_secret("sensitive data", self.KEY)
        raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("utf-8")))
        raw[-1] ^= 0x01  # Flip 1 bit in tag
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode("utf-8")

        with pytest.raises(SecurityError) as exc_info:
            decrypt_secret(tampered, self.KEY)
        assert "Authentication tag mismatch" in exc_info.value.message

    def test_decrypt_corrupted_base64_raises_security_error(self) -> None:
        with pytest.raises(SecurityError) as exc_info:
            decrypt_secret("not-valid-base64-!@#$%", self.KEY)
        assert "Corrupted base64" in exc_info.value.message

    def test_decrypt_payload_too_short_raises_security_error(self) -> None:
        short_payload = base64.urlsafe_b64encode(b"short").decode("utf-8")
        with pytest.raises(SecurityError) as exc_info:
            decrypt_secret(short_payload, self.KEY)
        assert "payload too short" in exc_info.value.message
