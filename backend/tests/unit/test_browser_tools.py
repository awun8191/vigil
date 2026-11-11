"""Unit tests for browser_navigate and browser_extract tools."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from src.config.settings import Settings
from src.modules.runs.tools import ToolContext, invoke_tool
from src.modules.runs.tools.browser import (
    BrowserExtractArgs,
    BrowserNavigateArgs,
    browser_extract,
    browser_navigate,
)


def make_tool_context(delivery_dir: Path) -> ToolContext:
    """Build lightweight tool context for tests."""
    runner = SimpleNamespace()
    return ToolContext(runner=runner, container_id="test-container", delivery_dir=delivery_dir)


@pytest.mark.asyncio
async def test_browser_navigate_fallback(tmp_path: Path) -> None:
    """Verify browser_navigate fallback produces a valid PNG screenshot file in delivery_dir."""
    context = make_tool_context(tmp_path)
    args = BrowserNavigateArgs(url="https://example.com", capture_screenshot=True)

    result = await browser_navigate(args, context)
    assert result.ok is True
    assert result.content["status"] == 200
    assert result.content["url"] == "https://example.com"
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
async def test_browser_extract_fallback(tmp_path: Path) -> None:
    """Verify browser_extract fallback returns clean markdown representation."""
    context = make_tool_context(tmp_path)
    args = BrowserExtractArgs(url="https://example.com", format="markdown")

    result = await browser_extract(args, context)
    assert result.ok is True
    assert result.content["status"] == 200
    assert "https://example.com" in result.content["markdown"]
    assert result.content.get("mock") is True


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
