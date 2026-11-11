"""
Unit-level adversarial challenge test suite for Milestone 4:
Cloud Browser, Groq Whisper STT, and Key Vault Integrations.

Focus Vectors:
1. Key Vault:
   - Empty keys, keys with whitespace, special characters, non-ASCII characters.
   - Long secrets (e.g. 100KB), short secrets (1-8 chars), binary-like secrets.
   - Multi-tenant boundary checks (user A cannot delete/read user B's secret).
   - Encryption integrity: verify ciphertext is genuine AES-256-GCM and not plaintext.
2. Browser Tools:
   - Malformed URLs, missing protocols, localhost/internal URLs, empty URLs.
   - Screenshot delivery directory validation (does PNG file actually exist, valid header bytes `\\x89PNG`).
   - Mock fallback behavior when `FIRECRAWL_API_KEY` is None or invalid.
3. Voice STT:
   - Zero-byte audio files, huge audio files, corrupted audio formats.
   - Mock fallback behavior when `GROQ_API_KEY` is None or invalid.
   - Concurrent transcription requests.
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
from src.dependencies import get_current_user, get_key_vault_service
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
from src.modules.settings.vault import (
    InMemoryVaultStore,
    KeyVaultService,
    mask_secret_value,
)

VOICE_AUTH = {"Authorization": "Bearer dev"}


def isolate_vault(app) -> None:
    """Keep the dev database vault out of a test that assumes no stored key."""
    app.dependency_overrides[get_key_vault_service] = lambda: KeyVaultService(
        settings=Settings(), store=InMemoryVaultStore()
    )


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_vault_storage() -> None:
    """Clear in-memory vault storage before and after each test."""
    KeyVaultService.clear_all()
    yield
    KeyVaultService.clear_all()


def make_tool_context(delivery_dir: Path) -> ToolContext:
    """Build lightweight tool context for tests."""
    runner = SimpleNamespace()
    return ToolContext(runner=runner, container_id="test-container-m4", delivery_dir=delivery_dir)


def parse_png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse width and height from PNG IHDR chunk (bytes 16..24)."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG magic header"
    assert data[12:16] == b"IHDR", f"Expected IHDR chunk, got {data[12:16]!r}"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# ===========================================================================
# 1. KEY VAULT EMPIRICAL ADVERSARIAL TESTS
# ===========================================================================

class TestKeyVaultAdversarial:
    """Adversarial testing of KeyVaultService and AES-256-GCM crypto primitives."""

    def test_key_vault_empty_and_whitespace_keys_rejected(self) -> None:
        """Verify empty and whitespace-only keys are strictly rejected with ValueError."""
        vault = KeyVaultService(settings=Settings())
        user_id = uuid4()

        invalid_keys = ["", " ", "   ", "\t", "\n", " \t \n "]
        for k in invalid_keys:
            with pytest.raises(ValueError, match="Key name cannot be empty"):
                vault.set_secret_sync(user_id, k, "valid_value")

    def test_key_vault_whitespace_padding_stripped(self) -> None:
        """Verify surrounding whitespace in key names is stripped cleanly."""
        vault = KeyVaultService(settings=Settings())
        user_id = uuid4()

        vault.set_secret_sync(user_id, "   API_SECRET_KEY   ", "super_secret_val")
        # Lookup with clean or padded key retrieves value
        assert vault.get_secret_sync(user_id, "API_SECRET_KEY") == "super_secret_val"
        assert vault.get_secret_sync(user_id, "   API_SECRET_KEY   ") == "super_secret_val"

        # Verify key_name in storage is stored trimmed
        listed = vault.list_secrets_sync(user_id)
        assert len(listed) == 1
        assert listed[0].key_name == "API_SECRET_KEY"

    def test_key_vault_special_and_non_ascii_keys(self) -> None:
        """Verify keys with special symbols, non-ASCII characters, and complex UTF-8 strings."""
        vault = KeyVaultService(settings=Settings())
        user_id = uuid4()

        special_keys = {
            "KEY_WITH_DOTS.AND_DASHES-AND_UNDERSCORES": "val1",
            "KEY/WITH/FORWARD/SLASHES": "val2",
            "KEY::WITH::COLONS": "val3",
            "ключ_безопасности_123": "val_cyrillic",
            "clé_d_authentification": "val_french",
            "密钥_2026_测试": "val_chinese",
            "🔑_TOKEN_EMOJI_🛡️": "val_emoji",
            "KEY WITH SPACES INSIDE": "val_internal_spaces",
            "!@#$%^&*()_+~`": "val_punctuation",
        }

        for k, v in special_keys.items():
            vault.set_secret_sync(user_id, k, v)

        for k, v in special_keys.items():
            retrieved = vault.get_secret_sync(user_id, k)
            assert retrieved == v, f"Failed for key {k!r}"

        # Listing should return all entries
        listed = vault.list_secrets_sync(user_id)
        assert len(listed) == len(special_keys)

    def test_key_vault_variable_length_secrets(self) -> None:
        """Verify secrets of variable lengths: empty, 1-8 chars, 9 chars, 10KB, 100KB."""
        vault = KeyVaultService(settings=Settings())
        user_id = uuid4()

        # 1. Empty secret
        vault.set_secret_sync(user_id, "EMPTY", "")
        assert vault.get_secret_sync(user_id, "EMPTY") == ""

        # 2. Short secrets (1 to 8 chars)
        for length in range(1, 9):
            k = f"SHORT_{length}"
            v = "X" * length
            vault.set_secret_sync(user_id, k, v)
            assert vault.get_secret_sync(user_id, k) == v
            assert mask_secret_value(v) == "****"

        # 3. Exactly 9 chars: prefix 3 + '****' + suffix 4
        vault.set_secret_sync(user_id, "EXACT_9", "ABC12DEFG")
        assert vault.get_secret_sync(user_id, "EXACT_9") == "ABC12DEFG"
        assert mask_secret_value("ABC12DEFG") == "ABC****DEFG"

        # 4. Long secrets: 10KB and 100KB
        ten_kb = "T" * 10_240
        hundred_kb = "H" * 102_400
        vault.set_secret_sync(user_id, "KEY_10K", ten_kb)
        vault.set_secret_sync(user_id, "KEY_100K", hundred_kb)

        assert vault.get_secret_sync(user_id, "KEY_10K") == ten_kb
        assert vault.get_secret_sync(user_id, "KEY_100K") == hundred_kb

        # Verify list_secrets handles 100KB without issue
        listed = vault.list_secrets_sync(user_id)
        dto_100k = next(s for s in listed if s.key_name == "KEY_100K")
        assert dto_100k.masked_value == "HHH****HHHH"

    def test_key_vault_binary_and_escape_secrets(self) -> None:
        """Verify binary-like secrets: null bytes, newlines, carriage returns, tabs, escape codes."""
        vault = KeyVaultService(settings=Settings())
        user_id = uuid4()

        binary_like = "header\x00nullbyte\r\n\t\x1b[31mred\x1b[0m\\n\\r\\t\\0"
        vault.set_secret_sync(user_id, "BINARY_LIKE", binary_like)
        assert vault.get_secret_sync(user_id, "BINARY_LIKE") == binary_like

    def test_key_vault_multi_tenant_boundaries(self) -> None:
        """Verify strict multi-tenant boundaries: User A cannot read, overwrite, or delete User B's secret."""
        vault = KeyVaultService(settings=Settings())
        user_a = uuid4()
        user_b = uuid4()

        # Both users store secret with identical key name
        vault.set_secret_sync(user_a, "API_KEY", "user_a_private_key_1111")
        vault.set_secret_sync(user_b, "API_KEY", "user_b_private_key_2222")

        # Exclusive secret for user A
        vault.set_secret_sync(user_a, "A_ONLY", "confidential_to_a")

        # 1. User B cannot read user A's exclusive secret
        assert vault.get_secret_sync(user_b, "A_ONLY") is None

        # 2. User B cannot delete user A's exclusive secret
        assert vault.delete_secret_sync(user_b, "A_ONLY") is False
        assert vault.get_secret_sync(user_a, "A_ONLY") == "confidential_to_a"

        # 3. User B cannot see user A's exclusive secret in list
        user_b_secrets = [s.key_name for s in vault.list_secrets_sync(user_b)]
        assert "A_ONLY" not in user_b_secrets
        assert "API_KEY" in user_b_secrets

        # 4. User B deleting 'API_KEY' does not affect user A's 'API_KEY'
        assert vault.delete_secret_sync(user_b, "API_KEY") is True
        assert vault.get_secret_sync(user_b, "API_KEY") is None
        assert vault.get_secret_sync(user_a, "API_KEY") == "user_a_private_key_1111"

    def test_key_vault_aes_gcm_cryptographic_integrity(self) -> None:
        """Empirically verify AES-256-GCM authenticated encryption properties."""
        key = "01234567890123456789012345678901"  # 32 bytes
        plaintext = "verifiably-secret-production-credential"

        # 1. Verify ciphertext is encrypted, not plaintext
        c1 = encrypt_secret(plaintext, key)
        assert plaintext not in c1
        assert c1 != plaintext

        # 2. Verify nonce uniqueness: same plaintext + same key produce different ciphertexts
        c2 = encrypt_secret(plaintext, key)
        assert c1 != c2, "Nonce reuse vulnerability! Two encryptions produced identical ciphertext."

        # 3. Verify wire structure: 12 bytes nonce + ciphertext + 16 bytes tag
        raw_payload = base64.urlsafe_b64decode(c1.encode("utf-8"))
        assert len(raw_payload) == 12 + len(plaintext.encode("utf-8")) + 16

        # 4. Decryption recovers original plaintext
        assert decrypt_secret(c1, key) == plaintext
        assert decrypt_secret(c2, key) == plaintext

        # 5. Tamper resistance: modifying nonce (bytes 0..11) fails authentication
        tampered_nonce = bytearray(raw_payload)
        tampered_nonce[0] ^= 0x01
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(base64.urlsafe_b64encode(tampered_nonce).decode("utf-8"), key)

        # 6. Tamper resistance: modifying ciphertext body fails authentication
        tampered_body = bytearray(raw_payload)
        tampered_body[14] ^= 0x55
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(base64.urlsafe_b64encode(tampered_body).decode("utf-8"), key)

        # 7. Tamper resistance: modifying authentication tag (last 16 bytes) fails authentication
        tampered_tag = bytearray(raw_payload)
        tampered_tag[-1] ^= 0xFF
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(base64.urlsafe_b64encode(tampered_tag).decode("utf-8"), key)

        # 8. Decryption with wrong key fails authentication
        wrong_key = "different-32-byte-secret-key-12"
        with pytest.raises(SecurityError, match="Authentication tag mismatch"):
            decrypt_secret(c1, wrong_key)

        # 9. Short payload (< 28 bytes) rejected
        short_b64 = base64.urlsafe_b64encode(b"too-short-payload").decode("utf-8")
        with pytest.raises(SecurityError, match="too short for AES-GCM"):
            decrypt_secret(short_b64, key)

        # 10. Non-base64 corrupted string rejected with Corrupted base64
        with pytest.raises(SecurityError, match="Corrupted base64"):
            decrypt_secret("!!!This is not base64@@@", key)

    @pytest.mark.asyncio
    async def test_key_vault_rest_api_boundaries_and_validations(self) -> None:
        """Verify REST API: 404 on deleting other user's secret, 422 on invalid payloads."""
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

        # Store secret as User A
        vault = KeyVaultService(settings=Settings())
        vault.set_secret_sync(user_a, "USER_A_KEY", "user_a_secret_value")

        # Act as User B
        app.dependency_overrides[get_current_user] = lambda: user_b_dto
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User B deleting User A's key returns 404
            del_res = await client.delete("/api/v1/settings/vault/USER_A_KEY")
            assert del_res.status_code == 404

            # 2. User B listing secrets returns empty list
            list_res = await client.get("/api/v1/settings/vault")
            assert list_res.status_code == 200
            assert list_res.json() == []

            # 3. User B creating secret with empty key -> 422
            p1 = await client.post("/api/v1/settings/vault", json={"key": "", "value": "val"})
            assert p1.status_code == 422

            # 4. User B creating secret with empty value -> 422
            p2 = await client.post("/api/v1/settings/vault", json={"key": "K", "value": ""})
            assert p2.status_code == 422

            # 5. User B creating secret without body -> 422
            p3 = await client.post("/api/v1/settings/vault")
            assert p3.status_code == 422


# ===========================================================================
# 2. BROWSER TOOLS EMPIRICAL ADVERSARIAL TESTS
# ===========================================================================

class TestBrowserToolsAdversarial:
    """Adversarial testing of browser navigation, content extraction, and PNG artifacts."""

    @pytest.mark.asyncio
    async def test_browser_url_protocol_handling_and_normalization(self, tmp_path: Path) -> None:
        """Verify URL protocol autocompletion, preservation, and edge cases."""
        context = make_tool_context(tmp_path)

        # 1. Missing protocol -> auto-prepends https://
        res1 = await browser_navigate(
            BrowserNavigateArgs(url="example.com/docs", capture_screenshot=False),
            context,
        )
        assert res1.ok is True
        assert res1.content["url"] == "https://example.com/docs"

        # 2. Explicit http:// protocol is preserved
        res2 = await browser_navigate(
            BrowserNavigateArgs(url="http://internal.service.local:8080/api", capture_screenshot=False),
            context,
        )
        assert res2.ok is True
        assert res2.content["url"] == "http://internal.service.local:8080/api"

        # 3. Localhost / Loopback URL
        res3 = await browser_navigate(
            BrowserNavigateArgs(url="http://127.0.0.1:3000/dashboard", capture_screenshot=False),
            context,
        )
        assert res3.ok is True
        assert res3.content["url"] == "http://127.0.0.1:3000/dashboard"

        # 4. Complex URL with query params, fragments, and unicode
        res4 = await browser_navigate(
            BrowserNavigateArgs(
                url="https://site.org/search?q=测试&page=1#results-table",
                capture_screenshot=False,
            ),
            context,
        )
        assert res4.ok is True
        assert res4.content["url"] == "https://site.org/search?q=测试&page=1#results-table"

    @pytest.mark.asyncio
    async def test_browser_tools_argument_validation_and_empty_urls(self, tmp_path: Path) -> None:
        """Verify invalid and empty URL arguments are rejected by tool registry."""
        context = make_tool_context(tmp_path)

        # 1. Empty string URL
        res_empty = await invoke_tool("browser_navigate", {"url": ""}, context)
        assert res_empty.ok is False
        assert "invalid arguments" in res_empty.error

        # 2. Missing url parameter entirely
        res_missing = await invoke_tool("browser_navigate", {}, context)
        assert res_missing.ok is False
        assert "invalid arguments" in res_missing.error

        # 3. Extract tool with empty URL
        res_ext_empty = await invoke_tool("browser_extract", {"url": ""}, context)
        assert res_ext_empty.ok is False
        assert "invalid arguments" in res_ext_empty.error

    @pytest.mark.asyncio
    async def test_screenshot_delivery_dir_and_png_integrity(self, tmp_path: Path) -> None:
        """
        Verify screenshot generation:
        - Auto-creates delivery_dir even if deeply nested
        - PNG file actually exists on disk
        - File has valid PNG header bytes (\\x89PNG\\r\\n\\x1a\\n)
        - Valid 1x1 RGBA IHDR dimension chunk
        - capture_screenshot=False produces no artifact
        """
        nested_dir = tmp_path / "deeply" / "nested" / "output_artifacts"
        assert not nested_dir.exists()
        context = make_tool_context(nested_dir)

        # 1. With screenshot capture
        args_with_screen = BrowserNavigateArgs(url="https://target.com", capture_screenshot=True)
        res_with = await browser_navigate(args_with_screen, context)
        assert res_with.ok is True
        assert res_with.artifact_ref is not None

        artifact_file = Path(res_with.artifact_ref)
        assert artifact_file.is_file()
        assert artifact_file.parent == nested_dir

        file_bytes = artifact_file.read_bytes()
        assert len(file_bytes) == len(FALLBACK_PNG_BYTES)
        assert file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

        width, height = parse_png_dimensions(file_bytes)
        assert width == 1
        assert height == 1

        # 2. Without screenshot capture
        args_without_screen = BrowserNavigateArgs(url="https://target.com", capture_screenshot=False)
        res_without = await browser_navigate(args_without_screen, context)
        assert res_without.ok is True
        assert res_without.artifact_ref is None

    @pytest.mark.asyncio
    async def test_firecrawl_api_fallback_matrix(self, tmp_path: Path) -> None:
        """
        Adversarially verify fallback behavior across all failure scenarios:
        - FIRECRAWL_API_KEY is None
        - Invalid API key (401 Unauthorized)
        - Rate limited (429)
        - Upstream server errors (500, 502 HTML, 504)
        - Connection timeouts and connection drops
        - Empty or malformed screenshot responses
        """
        context = make_tool_context(tmp_path)
        args = BrowserNavigateArgs(url="https://adversarial-target.org", capture_screenshot=True)

        # 1. FIRECRAWL_API_KEY is None
        with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
            mock_settings.return_value = Settings(FIRECRAWL_API_KEY=None)
            res = await browser_navigate(args, context)
            assert res.ok is True
            assert res.content.get("mock") is True
            assert res.artifact_ref is not None
            assert Path(res.artifact_ref).is_file()

        # Upstream failure responses
        scenarios = [
            httpx.Response(401, json={"error": "Unauthorized"}, request=httpx.Request("POST", "http://fc")),
            httpx.Response(429, json={"error": "Rate limit exceeded"}, request=httpx.Request("POST", "http://fc")),
            httpx.Response(500, json={"error": "Internal Server Error"}, request=httpx.Request("POST", "http://fc")),
            httpx.Response(502, text="<html>502 Bad Gateway</html>", request=httpx.Request("POST", "http://fc")),
            httpx.Response(200, json={}, request=httpx.Request("POST", "http://fc")),
            httpx.Response(200, json={"data": {"screenshot": "data:image/png;base64,corrupted-not-b64!!!"}}, request=httpx.Request("POST", "http://fc")),
            httpx.TimeoutException("Read timeout after 30 seconds"),
            httpx.ConnectError("Connection refused"),
        ]

        for scenario in scenarios:
            with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
                mock_settings.return_value = Settings(
                    FIRECRAWL_API_KEY="fc-invalid-or-failing-key",
                    FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
                )
                if isinstance(scenario, Exception):
                    mock_post = AsyncMock(side_effect=scenario)
                else:
                    mock_post = AsyncMock(return_value=scenario)

                with patch("httpx.AsyncClient.post", mock_post):
                    result = await browser_navigate(args, context)
                    assert result.ok is True
                    assert result.artifact_ref is not None
                    screenshot = Path(result.artifact_ref)
                    assert screenshot.is_file()
                    data = screenshot.read_bytes()
                    assert data.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_browser_extract_fallback_on_errors(self, tmp_path: Path) -> None:
        """Verify browser_extract returns deterministic readable markdown on API failure."""
        context = make_tool_context(tmp_path)
        args = BrowserExtractArgs(url="https://news.ycombinator.com", format="markdown")

        with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                FIRECRAWL_API_KEY="fc-key",
                FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
            )
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.ConnectError("Failed")):
                result = await browser_extract(args, context)
                assert result.ok is True
                assert result.content["status"] == 200
                assert "Extracted readable content for https://news.ycombinator.com" in result.content["markdown"]
                assert result.content["mock"] is True


# ===========================================================================
# 3. VOICE STT EMPIRICAL ADVERSARIAL TESTS
# ===========================================================================

class TestVoiceSTTAdversarial:
    """Adversarial testing of Groq Whisper STT endpoint and audio edge cases."""

    @pytest.mark.asyncio
    async def test_voice_zero_byte_audio_file(self) -> None:
        """Verify 0-byte audio file does not crash the server and returns deterministic fallback."""
        app = create_app()
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        empty_file = {"file": ("empty.webm", io.BytesIO(b""), "audio/webm")}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/voice/transcribe", files=empty_file, headers=VOICE_AUTH
            )
            assert res.status_code == 200
            data = res.json()
            assert data["mock"] is True
            assert data["model"] == "whisper-large-v3-turbo-mock"
            assert "Simulated voice transcription" in data["text"]

    @pytest.mark.asyncio
    async def test_voice_huge_audio_file_stress(self) -> None:
        """Stress test endpoint with a large 8MB audio payload."""
        app = create_app()
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        large_payload = b"RIFF" + os.urandom(8 * 1024 * 1024)
        files = {"file": ("large_session.wav", io.BytesIO(large_payload), "audio/wav")}

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH)
            assert res.status_code == 200
            data = res.json()
            assert data["mock"] is True
            assert isinstance(data["text"], str)

    @pytest.mark.asyncio
    async def test_voice_corrupted_and_non_audio_payloads(self) -> None:
        """Verify corrupted formats (PDF, PNG, random binary noise, plain text) are safely handled."""
        app = create_app()
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        test_payloads = [
            ("noise.raw", os.urandom(2048), "application/octet-stream"),
            ("fake_audio.mp3", b"NOT_AN_MP3_HEADER_JUST_GARBAGE", "audio/mpeg"),
            ("document.pdf", b"%PDF-1.4 header bytes", "application/pdf"),
            ("image.png", FALLBACK_PNG_BYTES, "image/png"),
            ("script.py", b"print('not audio')", "text/x-python"),
        ]

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for fname, payload, ctype in test_payloads:
                files = {"file": (fname, io.BytesIO(payload), ctype)}
                res = await client.post("/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH)
                assert res.status_code == 200, f"Failed on content-type {ctype}"
                assert res.json()["mock"] is True

    @pytest.mark.asyncio
    async def test_voice_fallback_when_groq_key_is_none(self) -> None:
        """Verify immediate deterministic mock when GROQ_API_KEY is None."""
        app = create_app(Settings(GROQ_API_KEY=None))
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        files = {"file": ("recording.webm", io.BytesIO(b"webm_bytes"), "audio/webm")}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH)
            assert res.status_code == 200
            assert res.json()["mock"] is True

    @pytest.mark.asyncio
    async def test_voice_reports_a_rejected_groq_key(self) -> None:
        """A 401 from Groq is a real error, not a mock success."""
        app = create_app(Settings(GROQ_API_KEY="gsk_invalid_expired_token_12345"))
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        files = {"file": ("recording.webm", io.BytesIO(b"audio_bytes"), "audio/webm")}
        fake_401 = (401, {"error": {"message": "Invalid API Key provided"}})

        with patch("src.modules.voice.router._call_groq_whisper", new_callable=AsyncMock, return_value=fake_401):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH
                )
                assert res.status_code == 502
                data = res.json()
                assert data["code"] == "GROQ_AUTH_FAILED"
                assert "rejected" in data["message"].lower()
                assert "mock" not in data

    @pytest.mark.asyncio
    async def test_voice_reports_groq_upstream_errors(self) -> None:
        """Every upstream failure surfaces with a status, never a fake transcript."""
        app = create_app(Settings(GROQ_API_KEY="gsk_configured_key"))
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        error_cases = [
            (400, {"error": {"message": "Corrupted audio format"}}),
            (429, {"error": {"message": "Rate limit exceeded"}}),
            (500, {"error": {"message": "Groq server internal error"}}),
            (502, "<html><body>502 Bad Gateway</body></html>"),
            (503, "Service Unavailable"),
            httpx.TimeoutException("Read timeout"),
            httpx.ConnectError("Failed to resolve Groq host"),
        ]

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for case in error_cases:
                files = {"file": ("audio.webm", io.BytesIO(b"bytes"), "audio/webm")}
                if isinstance(case, Exception):
                    mock_func = AsyncMock(side_effect=case)
                else:
                    mock_func = AsyncMock(return_value=case)

                with patch("src.modules.voice.router._call_groq_whisper", mock_func):
                    res = await client.post(
                        "/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH
                    )
                    data = res.json()
                    if isinstance(case, httpx.TimeoutException):
                        assert res.status_code == 503, f"Failed on error case {case}"
                        assert data["code"] == "GROQ_TIMEOUT"
                    elif isinstance(case, httpx.ConnectError):
                        assert res.status_code == 502, f"Failed on error case {case}"
                        assert data["code"] == "GROQ_UNREACHABLE"
                    else:
                        assert res.status_code == 502, f"Failed on error case {case}"
                        assert data["code"] == "GROQ_UPSTREAM_ERROR"
                    assert "mock" not in data

    @pytest.mark.asyncio
    async def test_voice_missing_and_malformed_requests(self) -> None:
        """Verify 422 Unprocessable Entity when request body is missing or malformed."""
        app = create_app()
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. No payload
            res1 = await client.post(
                "/api/v1/voice/transcribe", headers=VOICE_AUTH
            )
            assert res1.status_code == 422

            # 2. JSON body instead of multipart
            res2 = await client.post(
                "/api/v1/voice/transcribe",
                json={"file": "not-file"},
                headers=VOICE_AUTH,
            )
            assert res2.status_code == 422

            # 3. Wrong field name ('recording' instead of 'file')
            res3 = await client.post(
                "/api/v1/voice/transcribe",
                files={"recording": ("audio.webm", io.BytesIO(b"bytes"), "audio/webm")},
                headers=VOICE_AUTH,
            )
            assert res3.status_code == 422

    @pytest.mark.asyncio
    async def test_voice_concurrent_transcriptions(self) -> None:
        """Stress-test 25 concurrent transcription requests simultaneously."""
        app = create_app()
        isolate_vault(app)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def send_transcribe(idx: int) -> dict:
                files = {
                    "file": (
                        f"voice_{idx}.webm",
                        io.BytesIO(f"audio_bytes_worker_{idx}".encode("utf-8")),
                        "audio/webm",
                    )
                }
                res = await client.post("/api/v1/voice/transcribe", files=files, headers=VOICE_AUTH)
                assert res.status_code == 200
                return res.json()

            results = await asyncio.gather(*(send_transcribe(i) for i in range(25)))
            assert len(results) == 25
            for r in results:
                assert r["mock"] is True
                assert r["model"] == "whisper-large-v3-turbo-mock"
                assert "Simulated voice transcription" in r["text"]
