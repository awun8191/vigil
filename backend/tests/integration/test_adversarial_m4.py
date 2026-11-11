"""
Adversarial challenge test suite for Milestone 4:
Cloud Browser, Groq Whisper STT, and Key Vault Integrations.

Empirically verifies:
1. Key Vault:
   - Empty secrets, whitespace keys, unicode (CJK, Arabic, Emojis, ZWJ, null bytes).
   - Long secrets (>10KB, 50KB, 100KB).
   - AES-256-GCM corruption, bit-flipping, invalid tags, short payloads, key mismatch.
   - Masking boundaries (0, 1, 7, 8, 9, 10, 100 chars).
   - Strict multi-tenant user isolation and concurrent tenant operations.
   - REST API 422 validations and 404 on cross-tenant deletion.
2. Browser Tools:
   - Missing/empty URLs, protocol autocompletion (https:// prefix).
   - Deterministic PNG generation: signature and IHDR dimensions (1x1 RGBA).
   - Firecrawl API failures: timeouts, connection drops, 5xx errors, empty JSON, corrupted screenshot data.
   - ToolContext delivery_dir auto-creation and valid artifact references.
3. Voice Transcription:
   - Non-audio payloads (plain text, PDF, PNG, random binary noise).
   - Zero-byte empty files and large payload stress (5MB).
   - Groq API failure matrix: 400, 401, 429, 500, 502 HTML, network timeout, connection drop.
   - Fallback mock JSON schema validity across all conditions.
   - Malformed request structures (missing file, wrong field name -> 422).
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.security import SecurityError, decrypt_secret, encrypt_secret
from src.config.settings import Settings
from src.dependencies import get_current_user
from src.main import create_app
from src.modules.auth.repository import UserDTO
from src.modules.runs.tools import ToolContext, invoke_tool
from src.modules.runs.tools.browser import (
    FALLBACK_PNG_BYTES,
    BrowserExtractArgs,
    BrowserNavigateArgs,
    browser_extract,
    browser_navigate,
)
from src.modules.settings.vault import KeyVaultService, mask_secret_value

VOICE_AUTH = {"Authorization": "Bearer dev"}


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_vault() -> None:
    """Ensure in-memory vault storage is wiped clean between each test."""
    KeyVaultService.clear_all()
    yield
    KeyVaultService.clear_all()


def make_tool_context(delivery_dir: Path) -> ToolContext:
    """Create test ToolContext with specified delivery directory."""
    runner = SimpleNamespace()
    return ToolContext(runner=runner, container_id="test-box-m4", delivery_dir=delivery_dir)


def parse_png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse width and height from PNG IHDR chunk (bytes 16..24)."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG magic header"
    # IHDR chunk starts at byte 12 ('IHDR'), width at 16..20, height at 20..24
    ihdr_marker = data[12:16]
    assert ihdr_marker == b"IHDR", f"Expected IHDR chunk, got {ihdr_marker!r}"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# ===========================================================================
# 1. KEY VAULT ADVERSARIAL TESTS
# ===========================================================================

class TestKeyVaultAdversarial:
    """Stress tests and boundary checks for KeyVaultService and cryptographic layer."""

    def test_masking_boundary_conditions(self) -> None:
        """
        Verify masking logic boundary behavior:
        - len <= 8: always '****'
        - len > 8: prefix(3) + '****' + suffix(4)
        """
        assert mask_secret_value("") == "****"
        assert mask_secret_value("1") == "****"
        assert mask_secret_value("1234567") == "****"
        assert mask_secret_value("12345678") == "****"
        # Exactly 9 characters: 3 prefix + '****' + 4 suffix
        assert mask_secret_value("123456789") == "123****6789"
        # Exactly 10 characters
        assert mask_secret_value("abcdefghij") == "abc****ghij"
        # 50 characters
        long_val = "x" * 50
        assert mask_secret_value(long_val) == "xxx****xxxx"
        # Emojis (each emoji is 1 Python str char in length)
        emojis_8 = "🔑🔐🛡️🚀🔥⭐🌟🎯"
        # Note: some emojis with modifiers or ZWJ may have varying len; test basic single-codepoint
        basic_emojis_8 = "😀😁😂😃😄😅😆😇"
        assert len(basic_emojis_8) == 8
        assert mask_secret_value(basic_emojis_8) == "****"

        basic_emojis_9 = "😀😁😂😃😄😅😆😇😈"
        assert len(basic_emojis_9) == 9
        assert mask_secret_value(basic_emojis_9) == "😀😁😂****😅😆😇😈"

    @pytest.mark.asyncio
    async def test_vault_empty_and_whitespace_keys_and_values(self) -> None:
        """Verify behavior with empty strings, whitespace, and invalid payloads."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        user_id = uuid4()

        # 1. Empty key name raises ValueError
        with pytest.raises(ValueError, match="Key name cannot be empty"):
            await vault.set_secret(user_id, "", "valid_value")

        with pytest.raises(ValueError, match="Key name cannot be empty"):
            await vault.set_secret(user_id, "   ", "valid_value")

        # 2. Key with surrounding whitespace is stripped cleanly
        await vault.set_secret(user_id, "  PADDED_KEY  ", "secret_val")
        assert await vault.get_secret(user_id, "PADDED_KEY") == "secret_val"
        assert await vault.get_secret(user_id, "  PADDED_KEY  ") == "secret_val"

        # 3. Empty secret value encrypts and decrypts cleanly at service level
        await vault.set_secret(user_id, "EMPTY_SECRET", "")
        decrypted_empty = await vault.get_secret(user_id, "EMPTY_SECRET")
        assert decrypted_empty == ""

        # Masking of empty secret in list_secrets
        secrets = await vault.list_secrets(user_id)
        empty_dto = next(s for s in secrets if s.key_name == "EMPTY_SECRET")
        assert empty_dto.masked_value == "****"

    @pytest.mark.asyncio
    async def test_vault_unicode_and_complex_payloads(self) -> None:
        """Verify UTF-8 resilience: CJK, Arabic, Cyrillic, Emojis, null bytes, special chars."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        user_id = uuid4()

        complex_secrets = {
            "CJK_SECRET": "密匙测试-东京-北京-2026-🌟",
            "ARABIC_SECRET": "مفتاح_سري_خاص_للغاية_١٢٣٤٥",
            "CYRILLIC_SECRET": "секретный_токен_безопасности_999",
            "EMOJI_ZWJ_SECRET": "👨‍👩‍👧‍👦-🚀-🛡️-🔑-🔥",
            "CONTROL_CHARS_SECRET": "line1\nline2\r\ttabbed\0nullbyte!@#$%^&*()_+",
        }

        for key, val in complex_secrets.items():
            await vault.set_secret(user_id, key, val)

        for key, expected_val in complex_secrets.items():
            retrieved = await vault.get_secret(user_id, key)
            assert retrieved == expected_val, f"Mismatch for unicode secret {key}"

        # Verify listing does not crash on complex unicode
        listed = await vault.list_secrets(user_id)
        assert len(listed) == len(complex_secrets)

    @pytest.mark.asyncio
    async def test_vault_large_payloads_stress(self) -> None:
        """Stress-test AES-GCM with secrets of 10KB, 50KB, and 100KB."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        user_id = uuid4()

        payload_10k = "A" * 10_240
        payload_50k = "B" * 51_200
        payload_100k = "C" * 102_400

        await vault.set_secret(user_id, "KEY_10K", payload_10k)
        await vault.set_secret(user_id, "KEY_50K", payload_50k)
        await vault.set_secret(user_id, "KEY_100K", payload_100k)

        assert await vault.get_secret(user_id, "KEY_10K") == payload_10k
        assert await vault.get_secret(user_id, "KEY_50K") == payload_50k
        assert await vault.get_secret(user_id, "KEY_100K") == payload_100k

        # Check list secrets properly masks huge payloads without hanging
        listed = await vault.list_secrets(user_id)
        dto_100k = next(s for s in listed if s.key_name == "KEY_100K")
        assert dto_100k.masked_value == f"CCC****CCCC"

    def test_vault_corrupted_aes_payloads_and_tampering(self) -> None:
        """Test cryptographic failure modes: bad base64, short payload, bit flips, bad key."""
        key = "01234567890123456789012345678901"
        plaintext = "super-sensitive-secret-token"
        ciphertext = encrypt_secret(plaintext, key)

        # 1. Non-base64 payload
        with pytest.raises(SecurityError, match="Corrupted base64"):
            decrypt_secret("!!!This is not base64@@@", key)

        # 2. Payload shorter than 28 bytes (12 nonce + 16 tag minimum)
        short_bytes = b"too_short"
        short_b64 = base64.urlsafe_b64encode(short_bytes).decode("utf-8")
        with pytest.raises(SecurityError, match="too short for AES-GCM"):
            decrypt_secret(short_b64, key)

        # 3. Bit-flip tampering in ciphertext body (authenticity violation)
        raw_payload = bytearray(base64.urlsafe_b64decode(ciphertext))
        # Flip a bit in the middle of ciphertext
        raw_payload[15] ^= 0xFF
        tampered_b64 = base64.urlsafe_b64encode(raw_payload).decode("utf-8")
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(tampered_b64, key)

        # 4. Bit-flip tampering in tag (last 16 bytes)
        raw_payload_tag = bytearray(base64.urlsafe_b64decode(ciphertext))
        raw_payload_tag[-1] ^= 0x01
        tampered_tag_b64 = base64.urlsafe_b64encode(raw_payload_tag).decode("utf-8")
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(tampered_tag_b64, key)

        # 5. Decryption with wrong secret key
        wrong_key = "different-secret-key-32-bytes-long"
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(ciphertext, wrong_key)

    @pytest.mark.asyncio
    async def test_vault_corrupted_storage_does_not_crash_listing(self) -> None:
        """Verify list_secrets gracefully masks corrupted records as '****' instead of raising 500."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        user_id = uuid4()

        # Store a valid secret
        await vault.set_secret(user_id, "VALID_KEY", "valid-secret-token-12345")
        # Store another valid secret, then manually tamper with its ciphertext in storage
        await vault.set_secret(user_id, "CORRUPTED_KEY", "will-be-corrupted-12345")

        record = KeyVaultService._fallback_store.rows[(user_id, "CORRUPTED_KEY")]
        record.ciphertext = "tampered-garbage-b64"

        # Listing should not raise an unhandled exception
        listed = await vault.list_secrets(user_id)
        assert len(listed) == 2

        valid_dto = next(s for s in listed if s.key_name == "VALID_KEY")
        assert valid_dto.masked_value == "val****2345"

        corrupted_dto = next(s for s in listed if s.key_name == "CORRUPTED_KEY")
        # Fallback mask on decryption failure
        assert corrupted_dto.masked_value == "****"

    @pytest.mark.asyncio
    async def test_vault_multi_tenant_isolation_and_cross_user_boundaries(self) -> None:
        """Adversarially verify that User B cannot access, overwrite, or delete User A's secrets."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        user_a = uuid4()
        user_b = uuid4()

        shared_key_name = "SHARED_INTEGRATION_KEY"
        await vault.set_secret(user_a, shared_key_name, "secret-user-A-only-1111")
        await vault.set_secret(user_b, shared_key_name, "secret-user-B-only-2222")

        # Exclusive secret for user A
        await vault.set_secret(user_a, "USER_A_EXCLUSIVE", "confidential-A")

        # 1. User B direct lookup for User A's exclusive secret
        assert await vault.get_secret(user_b, "USER_A_EXCLUSIVE") is None

        # 2. User B cannot delete User A's exclusive secret
        deleted = await vault.delete_secret(user_b, "USER_A_EXCLUSIVE")
        assert deleted is False
        assert await vault.get_secret(user_a, "USER_A_EXCLUSIVE") == "confidential-A"

        # 3. User B cannot see User A's exclusive secret in list_secrets
        user_b_secrets = await vault.list_secrets(user_b)
        b_key_names = [s.key_name for s in user_b_secrets]
        assert "USER_A_EXCLUSIVE" not in b_key_names
        assert shared_key_name in b_key_names

        # 4. User B deleting their own shared_key_name does not delete User A's secret
        deleted_b_shared = await vault.delete_secret(user_b, shared_key_name)
        assert deleted_b_shared is True
        assert await vault.get_secret(user_b, shared_key_name) is None
        # User A's secret remains intact
        assert await vault.get_secret(user_a, shared_key_name) == "secret-user-A-only-1111"

    @pytest.mark.asyncio
    async def test_vault_concurrent_tenant_operations(self) -> None:
        """Stress-test concurrent read/write operations across 10 distinct tenants."""
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        users = [uuid4() for _ in range(10)]

        async def tenant_worker(uid: any, idx: int) -> None:
            for k in range(5):
                key = f"KEY_{k}"
                val = f"val_{idx}_{k}_{'x'*20}"
                await vault.set_secret(uid, key, val)
                retrieved = await vault.get_secret(uid, key)
                assert retrieved == val
                listed = await vault.list_secrets(uid)
                assert len(listed) >= 1

        await asyncio.gather(*(tenant_worker(uid, i) for i, uid in enumerate(users)))

        # Verify each user has exactly 5 keys stored
        for i, uid in enumerate(users):
            secrets = await vault.list_secrets(uid)
            assert len(secrets) == 5
            for k in range(5):
                assert await vault.get_secret(uid, f"KEY_{k}") == f"val_{i}_{k}_{'x'*20}"

    @pytest.mark.asyncio
    async def test_vault_rest_cross_tenant_rejection_and_422(self) -> None:
        """Verify REST API enforces 404 on deleting someone else's secret and 422 on invalid bodies."""
        app = create_app()
        user_a = uuid4()
        user_b = uuid4()
        now = datetime.now(timezone.utc)

        user_b_dto = UserDTO(
            id=user_b,
            display_name="User B",
            settings={},
            created_at=now,
            updated_at=now,
        )

        # Pre-seed secret belonging to User A
        settings = Settings()
        vault = KeyVaultService(settings=settings)
        await vault.set_secret(user_a, "TOP_SECRET_A", "classified-data-12345")

        # Override user to User B
        app.dependency_overrides[get_current_user] = lambda: user_b_dto
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User B tries to delete User A's secret -> 404 Not Found
            del_res = await client.delete("/api/v1/settings/vault/TOP_SECRET_A")
            assert del_res.status_code == 404

            # 2. User B lists secrets -> empty list
            list_res = await client.get("/api/v1/settings/vault")
            assert list_res.status_code == 200
            assert list_res.json() == []

            # 3. Validation failure: empty key string
            post_invalid_key = await client.post("/api/v1/settings/vault", json={"key": "", "value": "val"})
            assert post_invalid_key.status_code == 422

            # 4. Validation failure: empty value string
            post_invalid_val = await client.post("/api/v1/settings/vault", json={"key": "KEY", "value": ""})
            assert post_invalid_val.status_code == 422

            # 5. Missing fields
            post_missing = await client.post("/api/v1/settings/vault", json={})
            assert post_missing.status_code == 422


# ===========================================================================
# 2. BROWSER TOOLS ADVERSARIAL TESTS
# ===========================================================================

class TestBrowserToolsAdversarial:
    """Stress tests, failure modes, and binary validation for browser tools."""

    @pytest.mark.asyncio
    async def test_png_screenshot_byte_and_dimension_validation(self, tmp_path: Path) -> None:
        """
        Verify screenshot generation in delivery_dir produces:
        - Valid PNG 8-byte magic header
        - Correct 1x1 RGBA IHDR dimension chunk
        - Valid file path on disk
        """
        nested_dir = tmp_path / "deeply" / "nested" / "delivery_dir"
        context = make_tool_context(nested_dir)
        args = BrowserNavigateArgs(url="https://example.com/check-png", capture_screenshot=True)

        result = await browser_navigate(args, context)
        assert result.ok is True
        assert result.artifact_ref is not None

        artifact_path = Path(result.artifact_ref)
        assert artifact_path.is_file()
        assert artifact_path.parent == nested_dir

        file_bytes = artifact_path.read_bytes()
        assert len(file_bytes) == len(FALLBACK_PNG_BYTES)
        assert file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

        # Parse and verify dimensions from binary header
        width, height = parse_png_dimensions(file_bytes)
        assert width == 1
        assert height == 1

    @pytest.mark.asyncio
    async def test_browser_navigate_url_normalization(self, tmp_path: Path) -> None:
        """Verify URL auto-prefixing with https:// when schema is omitted."""
        context = make_tool_context(tmp_path)

        # 1. Without scheme
        args1 = BrowserNavigateArgs(url="github.com/trending", capture_screenshot=False)
        res1 = await browser_navigate(args1, context)
        assert res1.ok is True
        assert res1.content["url"] == "https://github.com/trending"

        # 2. With http scheme preserved
        args2 = BrowserNavigateArgs(url="http://insecure.example.com", capture_screenshot=False)
        res2 = await browser_navigate(args2, context)
        assert res2.ok is True
        assert res2.content["url"] == "http://insecure.example.com"

        # 3. Unicode and complex URL
        args3 = BrowserNavigateArgs(url="https://example.com/search?q=测试&ref=nav#section-1", capture_screenshot=False)
        res3 = await browser_navigate(args3, context)
        assert res3.ok is True
        assert res3.content["url"] == "https://example.com/search?q=测试&ref=nav#section-1"

    @pytest.mark.asyncio
    async def test_firecrawl_network_failures_graceful_fallbacks(self, tmp_path: Path) -> None:
        """
        Verify that when FIRECRAWL_API_KEY is configured, any network issue
        (timeout, connect failure, 5xx status, malformed response) cleanly falls back
        to deterministic mock and saves a valid PNG screenshot.
        """
        context = make_tool_context(tmp_path)
        args = BrowserNavigateArgs(url="https://failing-target.com", capture_screenshot=True)

        failure_scenarios = [
            # Scenario A: Timeout
            httpx.TimeoutException("Read timed out after 30s"),
            # Scenario B: Connection error
            httpx.ConnectError("Connection refused by remote host"),
            # Scenario C: 500 Internal Server Error
            httpx.Response(status_code=500, request=httpx.Request("POST", "https://api.firecrawl.dev")),
            # Scenario D: 502 Bad Gateway
            httpx.Response(status_code=502, request=httpx.Request("POST", "https://api.firecrawl.dev")),
            # Scenario E: 200 OK with empty dictionary
            httpx.Response(status_code=200, json={}, request=httpx.Request("POST", "https://api.firecrawl.dev")),
            # Scenario F: 200 OK with malformed base64 screenshot
            httpx.Response(
                status_code=200,
                json={"success": True, "data": {"screenshot": "data:image/png;base64,!!!NotBase64!!!"}},
                request=httpx.Request("POST", "https://api.firecrawl.dev"),
            ),
        ]

        for failure_case in failure_scenarios:
            with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
                mock_settings.return_value = Settings(
                    FIRECRAWL_API_KEY="fc-live-key",
                    FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
                )

                if isinstance(failure_case, Exception):
                    mock_post = AsyncMock(side_effect=failure_case)
                else:
                    mock_post = AsyncMock(return_value=failure_case)

                with patch("httpx.AsyncClient.post", mock_post):
                    result = await browser_navigate(args, context)
                    assert result.ok is True, f"Failed on scenario {failure_case}"
                    assert result.artifact_ref is not None
                    screenshot_file = Path(result.artifact_ref)
                    assert screenshot_file.is_file()
                    # Verify PNG bytes
                    file_bytes = screenshot_file.read_bytes()
                    assert file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_browser_extract_firecrawl_failures_fallback(self, tmp_path: Path) -> None:
        """Verify browser_extract cleanly returns deterministic markdown on upstream errors."""
        context = make_tool_context(tmp_path)
        args = BrowserExtractArgs(url="https://docs.pytest.org/en/stable/", format="markdown")

        with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                FIRECRAWL_API_KEY="fc-test-key",
                FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
            )
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.TimeoutException("Timeout")):
                result = await browser_extract(args, context)
                assert result.ok is True
                assert result.content["status"] == 200
                assert "Extracted readable content" in result.content["markdown"]
                assert result.content["mock"] is True


# ===========================================================================
# 3. VOICE TRANSCRIPTION ADVERSARIAL TESTS
# ===========================================================================

class TestVoiceTranscriptionAdversarial:
    """Stress tests, non-audio handling, large payloads, and Groq API failure matrix."""

    @pytest.mark.asyncio
    async def test_transcribe_non_audio_file_formats(self) -> None:
        """
        Verify endpoint handles non-audio uploads (plain text, PDF, PNG, random noise)
        without crashing and returns fallback mock response.
        """
        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        test_payloads = [
            ("notes.txt", b"Plain text note content", "text/plain"),
            ("doc.pdf", b"%PDF-1.5 test document header", "application/pdf"),
            ("image.png", FALLBACK_PNG_BYTES, "image/png"),
            ("corrupted.bin", os.urandom(1024), "application/octet-stream"),
        ]

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for fname, content, ctype in test_payloads:
                files = {"file": (fname, io.BytesIO(content), ctype)}
                res = await client.post(
                    "/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH
                )
                assert res.status_code == 200, f"Failed for content type {ctype}"
                data = res.json()
                assert data["mock"] is True
                assert data["model"] == "whisper-large-v3-turbo-mock"
                assert "Simulated voice transcription" in data["text"]

    @pytest.mark.asyncio
    async def test_transcribe_empty_file_and_large_file_stress(self) -> None:
        """Verify endpoint behavior with 0-byte empty file and 5MB large payload."""
        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Empty file (0 bytes)
            files_empty = {"file": ("empty.webm", io.BytesIO(b""), "audio/webm")}
            res_empty = await client.post(
                "/api/v1/voice/transcribe", files=files_empty, headers=VOICE_AUTH
            )
            assert res_empty.status_code == 200
            assert res_empty.json()["mock"] is True

            # 2. Large file (5 MB simulated audio)
            large_bytes = b"RIFF" + (b"\x00" * (5 * 1024 * 1024 - 4))
            files_large = {"file": ("large_voice.webm", io.BytesIO(large_bytes), "audio/webm")}
            res_large = await client.post(
                "/api/v1/voice/transcribe", files=files_large, headers=VOICE_AUTH
            )
            assert res_large.status_code == 200
            assert res_large.json()["mock"] is True

    @pytest.mark.asyncio
    async def test_groq_api_upstream_error_matrix(self) -> None:
        """
        Verify that when GROQ_API_KEY is configured, every upstream failure mode
        surfaces as a real status instead of a fabricated transcript:
        - 400 Bad Request (invalid audio codec)
        - 401 Unauthorized (expired Groq key)
        - 429 Too Many Requests (rate limit)
        - 500 Internal Server Error
        - 502 Bad Gateway with raw HTML text body
        - Upstream connection timeout
        - Upstream connection drop
        """
        custom_settings = Settings(
            GROQ_API_KEY="gsk_live_test_key",
            GROQ_BASE_URL="https://api.groq.com/openai/v1",
        )
        app = create_app(custom_settings)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        upstream_cases = [
            (400, {"error": {"message": "Invalid audio file"}}),
            (401, {"error": {"message": "Invalid API Key"}}),
            (429, {"error": {"message": "Rate limit reached"}}),
            (500, {"error": {"message": "Internal server error"}}),
            (502, "<html><body>502 Bad Gateway from Cloudflare</body></html>"),
            # Exception cases
            httpx.TimeoutException("Groq API timed out"),
            httpx.ConnectError("Failed to connect to Groq endpoint"),
        ]

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for case in upstream_cases:
                files = {"file": ("test.webm", io.BytesIO(b"dummy-audio-bytes"), "audio/webm")}
                if isinstance(case, Exception):
                    mock_call = AsyncMock(side_effect=case)
                else:
                    mock_call = AsyncMock(return_value=case)

                with patch("src.modules.voice.router._call_groq_whisper", mock_call):
                    res = await client.post(
                        "/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH
                    )
                    data = res.json()
                    if isinstance(case, httpx.TimeoutException):
                        assert res.status_code == 503, f"Failed on case {case}"
                        assert data["code"] == "GROQ_TIMEOUT"
                    elif isinstance(case, httpx.ConnectError):
                        assert res.status_code == 502, f"Failed on case {case}"
                        assert data["code"] == "GROQ_UNREACHABLE"
                    elif isinstance(case, tuple) and case[0] in (401, 403):
                        assert res.status_code == 502, f"Failed on case {case}"
                        assert data["code"] == "GROQ_AUTH_FAILED"
                    else:
                        assert res.status_code == 502, f"Failed on case {case}"
                        assert data["code"] == "GROQ_UPSTREAM_ERROR"
                    assert "mock" not in data

    @pytest.mark.asyncio
    async def test_transcribe_malformed_http_requests(self) -> None:
        """Verify endpoint returns 422 when required multipart file is absent or misnamed."""
        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Missing body entirely
            res1 = await client.post(
                "/api/v1/voice/transcribe", headers=VOICE_AUTH
            )
            assert res1.status_code == 422

            # 2. JSON instead of multipart
            res2 = await client.post(
                "/api/v1/voice/transcribe",
                json={"file": "not-multipart"},
                headers=VOICE_AUTH,
            )
            assert res2.status_code == 422

            # 3. Wrong form field name ('audio' instead of 'file')
            res3 = await client.post(
                "/api/v1/voice/transcribe",
                files={"audio": ("audio.webm", io.BytesIO(b"audio-bytes"), "audio/webm")},
                headers=VOICE_AUTH,
            )
            assert res3.status_code == 422
