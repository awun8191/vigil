"""Unit tests for browser_navigate and browser_extract tools."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from src.config.settings import Settings
from src.modules.runs.tools import ToolContext, invoke_tool
from src.modules.runs.tools.browser import (
    FALLBACK_PNG_BYTES,
    BrowserExtractArgs,
    BrowserNavigateArgs,
    _firecrawl_key,
    browser_extract,
    browser_navigate,
)


def make_tool_context(delivery_dir: Path, secrets: dict[str, str] | None = None) -> ToolContext:
    """Build lightweight tool context for tests."""
    runner = SimpleNamespace()
    return ToolContext(
        runner=runner,
        container_id="test-container",
        delivery_dir=delivery_dir,
        secrets=secrets or {},
    )


@pytest.mark.asyncio
async def test_browser_navigate_fallback(tmp_path: Path) -> None:
    """Verify browser_navigate fallback produces a valid PNG screenshot file in delivery_dir."""
    context = make_tool_context(tmp_path)
    args = BrowserNavigateArgs(url="https://example.com", capture_screenshot=True)

    result = await browser_navigate(args, context)
    assert result.ok is True
    assert result.content["status"] == 200
    assert result.content["url"] == "https://example.com"
    assert "No FIRECRAWL_API_KEY" in result.content["reason"]
    assert result.artifact_ref is not None

    screenshot_file = Path(result.artifact_ref)
    assert screenshot_file.is_file()
    assert screenshot_file.parent == tmp_path
    # Verify valid PNG signature
    data = screenshot_file.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_browser_navigate_without_screenshot(tmp_path: Path) -> None:
    """Verify browser_navigate with capture_screenshot=False."""
    context = make_tool_context(tmp_path)
    args = BrowserNavigateArgs(url="https://example.com", capture_screenshot=False)

    result = await browser_navigate(args, context)
    assert result.ok is True
    assert result.content["status"] == 200
    assert result.artifact_ref is None


@pytest.mark.asyncio
async def test_browser_navigate_reports_a_rejected_key(tmp_path: Path) -> None:
    """A configured key that Firecrawl rejects says so, instead of a bare mock."""
    context = make_tool_context(tmp_path, secrets={"FIRECRAWL_API_KEY": "fc-rejected-key"})
    args = BrowserNavigateArgs(url="https://example.com", capture_screenshot=False)
    rejected = httpx.Response(
        401,
        json={"error": "Unauthorized"},
        request=httpx.Request("POST", "https://api.firecrawl.dev/v2/interact"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=rejected):
        result = await browser_navigate(args, context)

    assert result.ok is True
    assert result.content["mock"] is True
    assert "rejected" in result.content["reason"].lower()
    assert "FIRECRAWL_API_KEY" in result.content["reason"]


@pytest.mark.asyncio
async def test_browser_extract_reports_a_rejected_key(tmp_path: Path) -> None:
    """browser_extract carries the same truthful reason for a rejected key."""
    context = make_tool_context(tmp_path, secrets={"FIRECRAWL_API_KEY": "fc-rejected-key"})
    args = BrowserExtractArgs(url="https://example.com", format="markdown")
    rejected = httpx.Response(
        401,
        json={"error": "Unauthorized"},
        request=httpx.Request("POST", "https://api.firecrawl.dev/v1/scrape"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=rejected):
        result = await browser_extract(args, context)

    assert result.ok is True
    assert result.content["mock"] is True
    assert "rejected" in result.content["reason"].lower()


@pytest.mark.asyncio
async def test_browser_extract_fallback(tmp_path: Path) -> None:
    """Verify browser_extract fallback returns clean markdown representation."""
    context = make_tool_context(tmp_path)
    args = BrowserExtractArgs(url="https://example.com", format="markdown")

    result = await browser_extract(args, context)
    assert result.ok is True
    assert result.content["status"] == 200
    assert "https://example.com" in result.content["markdown"]
    assert result.content.get("mock") is True
    assert "No FIRECRAWL_API_KEY" in result.content["reason"]


@pytest.mark.asyncio
async def test_browser_tools_invocation_through_registry(tmp_path: Path) -> None:
    """Verify tools can be called through the central invoke_tool entrypoint."""
    context = make_tool_context(tmp_path)

    # 1. browser_navigate
    nav_res = await invoke_tool("browser_navigate", {"url": "https://docs.python.org"}, context)
    assert nav_res.ok is True
    assert nav_res.content["url"] == "https://docs.python.org"
    assert nav_res.artifact_ref is not None

    # 2. browser_extract
    ext_res = await invoke_tool("browser_extract", {"url": "https://docs.python.org"}, context)
    assert ext_res.ok is True
    assert "https://docs.python.org" in ext_res.content["markdown"]


@pytest.mark.asyncio
async def test_browser_tools_argument_validation(tmp_path: Path) -> None:
    """Verify invalid arguments produce clear ToolResult failures."""
    context = make_tool_context(tmp_path)

    # Empty URL or missing URL fails validation
    res1 = await invoke_tool("browser_navigate", {}, context)
    assert res1.ok is False
    assert "invalid arguments" in res1.error

    res2 = await invoke_tool("browser_extract", {"url": ""}, context)
    assert res2.ok is False
    assert "invalid arguments" in res2.error


@pytest.mark.asyncio
async def test_firecrawl_live_session_returns_the_browser_window(tmp_path: Path) -> None:
    """A live session returns its view URLs and a screenshot of the same page."""
    context = make_tool_context(tmp_path, secrets={"FIRECRAWL_API_KEY": "fc-vault-key"})
    args = BrowserNavigateArgs(url="example.com", capture_screenshot=True)

    create = httpx.Response(
        status_code=200,
        json={
            "success": True,
            "id": "sess-123",
            "liveViewUrl": "https://liveview.firecrawl.dev/sess-123",
            "interactiveLiveViewUrl": "https://liveview.firecrawl.dev/sess-123?interactive=1",
            "expiresAt": "2030-01-01T00:00:00Z",
        },
        request=httpx.Request("POST", "https://api.firecrawl.dev/v2/interact"),
    )
    execute = httpx.Response(
        status_code=200,
        json={
            "success": True,
            "result": json.dumps(
                {
                    "title": "Example Domain",
                    "url": "https://example.com/",
                    "screenshot": base64.b64encode(FALLBACK_PNG_BYTES).decode(),
                }
            ),
        },
        request=httpx.Request(
            "POST", "https://api.firecrawl.dev/v2/interact/sess-123/execute"
        ),
    )

    with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            FIRECRAWL_API_KEY=None,
            FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[create, execute]) as mock_post:
            result = await browser_navigate(args, context)

    assert result.ok is True
    assert result.content["title"] == "Example Domain"
    assert result.content["url"] == "https://example.com/"
    assert result.content["live_view_url"] == "https://liveview.firecrawl.dev/sess-123"
    assert result.content["interactive_live_view_url"].startswith(
        "https://liveview.firecrawl.dev/sess-123"
    )
    assert result.content["session_expires_at"].startswith("2030-01-01")
    assert result.artifact_ref is not None
    assert Path(result.artifact_ref).read_bytes() == FALLBACK_PNG_BYTES
    assert mock_post.await_count == 2


def test_vault_secret_takes_precedence_over_settings(tmp_path: Path) -> None:
    """The vault key wins over an environment key when both exist."""
    context = make_tool_context(tmp_path, secrets={"FIRECRAWL_API_KEY": "fc-vault-key"})
    with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            FIRECRAWL_API_KEY="fc-env-key",
            FIRECRAWL_BASE_URL="https://api.firecrawl.dev/",
        )
        key, base_url = _firecrawl_key(context)

    assert key == "fc-vault-key"
    assert base_url == "https://api.firecrawl.dev"


@pytest.mark.asyncio
async def test_close_browser_session_frees_the_run_slot(tmp_path: Path) -> None:
    """Run cleanup closes the live session so the next run can create one."""
    from src.modules.runs.tools import browser as browser_module

    session = browser_module.LiveSession(
        id="sess-close",
        live_view_url="https://liveview/sess-close",
        interactive_live_view_url=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    browser_module._SESSIONS[str(tmp_path)] = session

    with patch.object(
        browser_module, "_delete_session", new_callable=AsyncMock
    ) as delete:
        await browser_module.close_browser_session(
            tmp_path, "fc-key", "https://api.firecrawl.dev"
        )

    assert str(tmp_path) not in browser_module._SESSIONS
    delete.assert_awaited_once()
    assert delete.await_args.args[3] == "sess-close"


@pytest.mark.asyncio
async def test_firecrawl_api_success_flow(tmp_path: Path) -> None:
    """Verify behavior when FIRECRAWL_API_KEY is configured and API responds successfully."""
    context = make_tool_context(tmp_path)
    args = BrowserNavigateArgs(url="https://news.ycombinator.com", capture_screenshot=True)

    fake_response = httpx.Response(
        status_code=200,
        json={
            "success": True,
            "data": {
                "screenshot": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "metadata": {"title": "Hacker News", "statusCode": 200},
            },
        },
        request=httpx.Request("POST", "https://api.firecrawl.dev/v1/scrape"),
    )

    with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            FIRECRAWL_API_KEY="fc-test-api-key",
            FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=fake_response):
            result = await browser_navigate(args, context)
            assert result.ok is True
            assert result.content["title"] == "Hacker News"
            assert result.artifact_ref is not None
            assert Path(result.artifact_ref).is_file()
