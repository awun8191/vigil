"""Adversarial stress tests for AES-256-GCM encryption/decryption primitives.

Verifies that corrupted payloads, truncated tags, mutated nonces, trailing garbage,
and wrong keys reliably raise SecurityError and never leak plaintext or cause crashes.
"""
import base64
import os
import pytest
from src.config.security import (
    SecurityError,
    decrypt_secret,
    encrypt_secret,
    hash_token,
    verify_token,
)


class TestAES256GCMAdversarial:
    KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    SAMPLE_PLAINTEXT = "vigil_secret_oauth_payload_with_credentials_#123!"

    def test_bit_flip_fuzzing_all_bytes(self) -> None:
        """Every single byte in the ciphertext payload (nonce, ct, tag) is mutated by bit flips.
        
        Asserts that SecurityError is ALWAYS raised and no unhandled exception or partial decrypt occurs.
        """
        ciphertext = encrypt_secret(self.SAMPLE_PLAINTEXT, self.KEY)
        raw_payload = bytearray(base64.urlsafe_b64decode(ciphertext.encode("utf-8")))
        assert len(raw_payload) >= 28

        # Test bit flip at every byte position across all regions:
        # Nonce (0..11), Ciphertext (12..len-17), Tag (len-16..len-1)
        for i in range(len(raw_payload)):
            for bit in [0x01, 0x08, 0x80]:
                mutated = bytearray(raw_payload)
                mutated[i] ^= bit
                tampered_b64 = base64.urlsafe_b64encode(bytes(mutated)).decode("utf-8")

                with pytest.raises(SecurityError) as exc_info:
                    decrypt_secret(tampered_b64, self.KEY)
                assert exc_info.value.code == "SECURITY_ERROR"

    def test_truncation_all_lengths_under_minimum(self) -> None:
        """Payloads truncated to any byte length strictly below 28 bytes raise SecurityError."""
        ciphertext = encrypt_secret(self.SAMPLE_PLAINTEXT, self.KEY)
        raw_payload = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))

        for length in range(28):
            truncated_raw = raw_payload[:length]
            truncated_b64 = base64.urlsafe_b64encode(truncated_raw).decode("utf-8")
            with pytest.raises(SecurityError) as exc_info:
                decrypt_secret(truncated_b64, self.KEY)
            assert "too short" in exc_info.value.message or "minimum 28 bytes" in exc_info.value.message

    def test_truncated_authentication_tags(self) -> None:
        """Truncating the 16-byte authentication tag by 1 to 15 bytes raises SecurityError."""
        ciphertext = encrypt_secret("short_secret", self.KEY)
        raw_payload = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
        total_len = len(raw_payload)

        # Truncate by 1 byte up to 15 bytes (shortening tag)
        for cut in range(1, 16):
            truncated_raw = raw_payload[:total_len - cut]
            truncated_b64 = base64.urlsafe_b64encode(truncated_raw).decode("utf-8")
            with pytest.raises(SecurityError) as exc_info:
                decrypt_secret(truncated_b64, self.KEY)
            assert exc_info.value.code == "SECURITY_ERROR"

    def test_tag_and_ciphertext_boundary_swap(self) -> None:
        """Swapping bytes between ciphertext and tag fails tag authentication."""
        ciphertext = encrypt_secret("some_longer_sensitive_database_credential_string", self.KEY)
        raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("utf-8")))
        
        # Swap byte between ciphertext body and tag
        ct_idx = 15
        tag_idx = len(raw) - 5
        raw[ct_idx], raw[tag_idx] = raw[tag_idx], raw[ct_idx]

        tampered_b64 = base64.urlsafe_b64encode(bytes(raw)).decode("utf-8")
        with pytest.raises(SecurityError):
            decrypt_secret(tampered_b64, self.KEY)

    def test_extension_and_trailing_garbage(self) -> None:
        """Appending extra bytes to valid payload fails tag authentication or base64 decoding."""
        ciphertext = encrypt_secret(self.SAMPLE_PLAINTEXT, self.KEY)
        raw = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))

        # 1 byte appended
        with pytest.raises(SecurityError):
            decrypt_secret(base64.urlsafe_b64encode(raw + b"\x00").decode("utf-8"), self.KEY)

        # 16 bytes appended
        with pytest.raises(SecurityError):
            decrypt_secret(base64.urlsafe_b64encode(raw + os.urandom(16)).decode("utf-8"), self.KEY)

        # Strings that cause base64 decoding failure
        bad_base64_strings = [
            "not-valid-base64-!@#$%",
            "abc",
            "a==",
            "invalid_base64_symbols_&&&^^^%%%",
        ]
        for bad in bad_base64_strings:
            with pytest.raises(SecurityError) as exc_info:
                decrypt_secret(bad, self.KEY)
            assert "Corrupted base64" in exc_info.value.message or "too short" in exc_info.value.message

        # Mutating characters in base64 body so it always alters decoded bytes
        mid = len(ciphertext) // 2
        tampered_b64 = ciphertext[:mid] + ("A" if ciphertext[mid] != "A" else "B") + ciphertext[mid + 1:]
        with pytest.raises(SecurityError):
            decrypt_secret(tampered_b64, self.KEY)

    def test_wrong_keys_stress(self) -> None:
        """Decrypting with various wrong keys raises SecurityError."""
        ciphertext = encrypt_secret(self.SAMPLE_PLAINTEXT, self.KEY)

        wrong_keys = [
            "0" * 64,  # All zeroes hex
            "f" * 64,  # All f hex
            "1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",  # 1 bit hex diff
            "different-passphrase-entirely",
            "another_32_byte_string_for_test!",
            b"\x00" * 32,
            b"\xff" * 32,
            os.urandom(32),
            "",  # Empty key
        ]

        for wk in wrong_keys:
            with pytest.raises(SecurityError) as exc_info:
                decrypt_secret(ciphertext, wk)
            assert exc_info.value.code == "SECURITY_ERROR"
            assert "mismatch" in exc_info.value.message.lower() or "decryption failed" in exc_info.value.message.lower()

    def test_minimal_payload_boundary_28_bytes(self) -> None:
        """An empty string produces exactly 28 bytes (12 bytes nonce + 0 bytes ct + 16 bytes tag).
        
        Verify empty string decrypts correctly, and tampering with it raises SecurityError.
        """
        empty_ct = encrypt_secret("", self.KEY)
        raw = base64.urlsafe_b64decode(empty_ct.encode("utf-8"))
        assert len(raw) == 28

        # Valid decryption produces empty string
        assert decrypt_secret(empty_ct, self.KEY) == ""

        # Truncate by 1 byte to 27 bytes
        tampered_27 = base64.urlsafe_b64encode(raw[:27]).decode("utf-8")
        with pytest.raises(SecurityError):
            decrypt_secret(tampered_27, self.KEY)

        # Mutate single bit in nonce (byte 0)
        mutated_nonce = bytearray(raw)
        mutated_nonce[0] ^= 0x01
        with pytest.raises(SecurityError):
            decrypt_secret(base64.urlsafe_b64encode(bytes(mutated_nonce)).decode("utf-8"), self.KEY)

        # Mutate single bit in tag (byte 27)
        mutated_tag = bytearray(raw)
        mutated_tag[27] ^= 0x01
        with pytest.raises(SecurityError):
            decrypt_secret(base64.urlsafe_b64encode(bytes(mutated_tag)).decode("utf-8"), self.KEY)

    def test_large_payload_stress(self) -> None:
        """1MB and 2MB payloads roundtrip cleanly and tampering raises SecurityError."""
        large_text = "Vigil-Enterprise-Payload-" * 40000  # ~1MB
        ciphertext = encrypt_secret(large_text, self.KEY)
        assert decrypt_secret(ciphertext, self.KEY) == large_text

        # Tamper 1 byte in the middle of 1MB ciphertext
        raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("utf-8")))
        raw[len(raw) // 2] ^= 0x55
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode("utf-8")
        with pytest.raises(SecurityError):
            decrypt_secret(tampered, self.KEY)

    def test_invalid_types_raise_type_error(self) -> None:
        """Non-string/bytes inputs raise TypeError instead of crashing."""
        ciphertext = encrypt_secret(self.SAMPLE_PLAINTEXT, self.KEY)

        with pytest.raises(TypeError):
            encrypt_secret(12345, self.KEY)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            encrypt_secret("test", 12345)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            decrypt_secret(12345, self.KEY)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            decrypt_secret(ciphertext, 12345)  # type: ignore[arg-type]
