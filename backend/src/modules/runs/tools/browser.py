"""Cloud browser navigation and content extraction tools powered by Firecrawl.

``browser_navigate`` drives a live Firecrawl browser session. The session
returns a live view URL that the web chat embeds, so the user watches the same
window the agent opened. A screenshot of the current page is written beside it
as the durable fallback for the chat history.

The tools read ``FIRECRAWL_API_KEY`` from the run owner's vault first. The
environment setting stays as the fallback for local runs and tests.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.config.settings import get_settings
from src.modules.runs.tools import Tool, ToolContext, ToolResult

logger = logging.getLogger("vigil.browser")

#: Valid 1x1 PNG fallback byte representation.
FALLBACK_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

#: Live browser sessions are reused across the tool calls of one run.
#: They are keyed by the run delivery directory, which is unique per run.
_SESSIONS: dict[str, "LiveSession"] = {}

#: Session lifetime caps. The chat can show the window after the run ends,
#: and the cap bounds the Firecrawl browser-minute billing.
SESSION_TTL_SECONDS = 900
SESSION_ACTIVITY_TTL_SECONDS = 600


class BrowserNavigateArgs(BaseModel):
    """Arguments for browser_navigate tool."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., min_length=1, description="Target webpage URL to navigate to and render.")
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot of the rendered page.")
    wait_for_selector: str | None = Field(
        default=None,
        description="Optional CSS selector to wait for before capturing screenshot.",
    )


class BrowserExtractArgs(BaseModel):
    """Arguments for browser_extract tool."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., min_length=1, description="Webpage URL to extract content from.")
    format: str = Field(default="markdown", description="Extraction format, default: markdown.")
    only_main_content: bool = Field(
        default=True,
        description="Whether to isolate main content, stripping navigation and footers.",
    )


@dataclass
class LiveSession:
    """One live Firecrawl browser session."""

    id: str
    live_view_url: str | None
    interactive_live_view_url: str | None
    expires_at: datetime | None

    def is_expired(self) -> bool:
        """Return True when the session lifetime cap has passed."""
        return self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc)


# --- helpers -----------------------------------------------------------------


def _normalize_url(url: str) -> str:
    """Return an absolute http(s) URL."""
    clean = url.strip()
    if not (clean.startswith("http://") or clean.startswith("https://")):
        clean = f"https://{clean}"
    return clean


def _firecrawl_key(context: ToolContext) -> tuple[str | None, str]:
    """Return the API key and base URL, with the vault taking precedence."""
    settings = get_settings()
    key = context.secrets.get("FIRECRAWL_API_KEY") or settings.FIRECRAWL_API_KEY
    return key, settings.FIRECRAWL_BASE_URL.rstrip("/")


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _firecrawl_error(response: httpx.Response) -> str:
    """Return a short, non-secret reason for a Firecrawl failure."""
    if response.status_code in (401, 403):
        return (
            "Firecrawl rejected the API key "
            f"(HTTP {response.status_code}). Update FIRECRAWL_API_KEY in Settings → Connectors."
        )
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error:
            detail = f": {error[:200]}"
        elif isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                detail = f": {message[:200]}"
    return f"Firecrawl returned HTTP {response.status_code}{detail}"


def _parse_expiry(value: Any) -> datetime | None:
    """Parse the Firecrawl ``expiresAt`` timestamp."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _session_fields(session: LiveSession | None) -> dict[str, str]:
    """Return the live view fields a tool result carries for the chat panel."""
    if session is None or session.is_expired():
        return {}
    fields: dict[str, str] = {"browser_session_id": session.id}
    if session.live_view_url:
        fields["live_view_url"] = session.live_view_url
    if session.interactive_live_view_url:
        fields["interactive_live_view_url"] = session.interactive_live_view_url
    if session.expires_at is not None:
        fields["session_expires_at"] = session.expires_at.isoformat()
    return fields


def _active_session(context: ToolContext) -> LiveSession | None:
    """Return the live session of this run, if it has not expired."""
    session = _SESSIONS.get(str(context.delivery_dir))
    if session is None or session.is_expired():
        return None
    return session


async def _delete_session(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], session_id: str
) -> None:
    """Close one session, best effort."""
    try:
        await client.delete(f"{base_url}/v2/interact/{session_id}", headers=headers)
    except Exception:  # noqa: BLE001 - cleanup never masks the tool result
        pass


async def close_browser_session(
    delivery_dir: str | Path,
    api_key: str | None,
    base_url: str | None = None,
) -> None:
    """Close the live session of one run and forget it.

    Firecrawl caps concurrent live sessions. A run that leaves one open
    starves the next run, so the executor closes it as part of run cleanup.
    """
    session = _SESSIONS.pop(str(delivery_dir), None)
    if session is None or not api_key:
        return
    base = (base_url or get_settings().FIRECRAWL_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _delete_session(client, base, _headers(api_key), session.id)


async def _ensure_session(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    key: str,
) -> LiveSession | None:
    """Return the reusable session of this run, or create one."""
    for stale_key, stale in list(_SESSIONS.items()):
        if stale.is_expired():
            _SESSIONS.pop(stale_key, None)

    existing = _SESSIONS.get(key)
    if existing is not None and not existing.is_expired():
        return existing
    if existing is not None:
        _SESSIONS.pop(key, None)
        await _delete_session(client, base_url, headers, existing.id)

    response = await client.post(
        f"{base_url}/v2/interact",
        headers=headers,
        json={
            "ttl": SESSION_TTL_SECONDS,
            "activityTtl": SESSION_ACTIVITY_TTL_SECONDS,
        },
    )
    if response.status_code != 200:
        logger.warning(
            "Firecrawl live session returned HTTP %d: %s",
            response.status_code,
            response.text[:200],
        )
        return None
    data = response.json()
    session_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or not session_id:
        logger.warning(
            "Firecrawl live session response had no id: %s", str(data)[:200]
        )
        return None
    session = LiveSession(
        id=session_id,
        live_view_url=(
            data.get("liveViewUrl") if isinstance(data.get("liveViewUrl"), str) else None
        ),
        interactive_live_view_url=(
            data.get("interactiveLiveViewUrl")
            if isinstance(data.get("interactiveLiveViewUrl"), str)
            else None
        ),
        expires_at=(
            _parse_expiry(data.get("expiresAt"))
            or datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
        ),
    )
    _SESSIONS[key] = session
    return session


def _navigation_code(url: str, wait_for_selector: str | None, screenshot: bool) -> str:
    """Build the Playwright code that opens a page in the live session."""
    lines = [
        f"await page.goto({json.dumps(url)}, {{ waitUntil: 'domcontentloaded', timeout: 30000 }});"
    ]
    if wait_for_selector:
        lines.append(
            f"await page.waitForSelector({json.dumps(wait_for_selector)}, "
            "{ timeout: 15000 }).catch(() => {});"
        )
    lines.append("const title = await page.title();")
    if screenshot:
        lines.append("const shot = (await page.screenshot({ type: 'png' })).toString('base64');")
        lines.append("JSON.stringify({ title, url: page.url(), screenshot: shot });")
    else:
        lines.append("JSON.stringify({ title, url: page.url() });")
    return "\n".join(lines)


async def _execute(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    code: str,
    *,
    language: str = "node",
    timeout: int = 45,
) -> dict[str, Any] | None:
    """Run code in a live session and return the response body."""
    response = await client.post(
        f"{base_url}/v2/interact/{session_id}/execute",
        headers=headers,
        json={"code": code, "language": language, "timeout": timeout},
    )
    if response.status_code != 200:
        return None
    data = response.json()
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    return data


def _execution_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Return the JSON object the execute call produced, from result or stdout."""
    for candidate in (result.get("result"), result.get("stdout")):
        if isinstance(candidate, dict):
            return candidate
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _decode_screenshot(value: Any) -> bytes | None:
    """Decode a base64 PNG payload and reject anything that is not a PNG."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.split(",", 1)[-1] if value.startswith("data:") else value
    try:
        data = base64.b64decode(raw)
    except Exception:  # noqa: BLE001 - a malformed payload is a missing screenshot
        return None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return data


def _screenshot_path(context: ToolContext, url: str) -> Path:
    """Return the delivery path for one page screenshot."""
    ts = int(datetime.now(timezone.utc).timestamp())
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return context.delivery_dir / f"screenshot_{ts}_{url_hash}.png"


def _scrape_screenshot(payload: dict[str, Any]) -> tuple[str, Any]:
    """Return the title and raw screenshot value from a v1 scrape response."""
    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    metadata = data.get("metadata")
    title = metadata.get("title") if isinstance(metadata, dict) else None
    return str(title or ""), data.get("screenshot")


async def _navigate_live(
    url: str,
    args: BrowserNavigateArgs,
    context: ToolContext,
    api_key: str,
    base_url: str,
) -> ToolResult | None:
    """Drive the live session to the URL and return the result, or None on failure."""
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=60.0) as client:
        session = await _ensure_session(
            client, base_url, headers, str(context.delivery_dir)
        )
        if session is None:
            return None
        payload = await _execute(
            client,
            base_url,
            headers,
            session.id,
            _navigation_code(url, args.wait_for_selector, args.capture_screenshot),
        )
        page = _execution_payload(payload) if payload is not None else None
        if page is None:
            return None

        title = str(page.get("title") or url)
        resolved_url = str(page.get("url") or url)
        content: dict[str, Any] = {
            "url": resolved_url,
            "title": title,
            "status": 200,
            **_session_fields(session),
        }
        artifact_ref: str | None = None
        if args.capture_screenshot:
            png_bytes = _decode_screenshot(page.get("screenshot")) or FALLBACK_PNG_BYTES
            target = _screenshot_path(context, resolved_url)
            target.write_bytes(png_bytes)
            artifact_ref = str(target)
        return ToolResult.success(content=content, artifact_ref=artifact_ref)


# --- tools -------------------------------------------------------------------


async def browser_navigate(args: BrowserNavigateArgs, context: ToolContext) -> ToolResult:
    """Open a URL in the live cloud browser and capture the page view."""
    api_key, base_url = _firecrawl_key(context)
    url_clean = _normalize_url(args.url)
    context.delivery_dir.mkdir(parents=True, exist_ok=True)
    reason = "No FIRECRAWL_API_KEY configured in the vault or environment."

    if api_key:
        reason = "Firecrawl request failed."
        try:
            live = await _navigate_live(url_clean, args, context, api_key, base_url)
            if live is not None:
                return live
        except Exception as exc:  # noqa: BLE001 - the scrape fallback still renders the page
            reason = f"Live browser session failed ({type(exc).__name__}): {exc}"
            logger.warning("Live browser session failed", exc_info=True)

        scrape_url = f"{base_url}/v1/scrape"
        body: dict[str, Any] = {"url": url_clean, "formats": ["screenshot"]}
        if args.wait_for_selector:
            body["waitFor"] = args.wait_for_selector

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(
                        scrape_url, headers=_headers(api_key), json=body
                    )
                    if res.status_code == 200:
                        data = res.json()
                        title, screenshot_data = _scrape_screenshot(
                            data if isinstance(data, dict) else {}
                        )
                        png_bytes = _decode_screenshot(screenshot_data)
                        if (
                            png_bytes is None
                            and isinstance(screenshot_data, str)
                            and screenshot_data.startswith("http")
                        ):
                            download = await client.get(screenshot_data)
                            if download.status_code == 200 and download.content:
                                png_bytes = download.content
                        if png_bytes is None:
                            png_bytes = FALLBACK_PNG_BYTES
                        content: dict[str, Any] = {
                            "url": url_clean,
                            "title": title or url_clean,
                            "status": 200,
                            **_session_fields(_active_session(context)),
                        }
                        if args.capture_screenshot:
                            target = _screenshot_path(context, url_clean)
                            target.write_bytes(png_bytes)
                            return ToolResult.success(
                                content=content, artifact_ref=str(target)
                            )
                        return ToolResult.success(content=content)
                    reason = _firecrawl_error(res)
                    break
            except httpx.TransportError as exc:
                reason = (
                    "Firecrawl request failed "
                    f"({type(exc).__name__}): {exc}"
                )
                logger.warning(
                    "Firecrawl scrape attempt %d failed: %s", attempt + 1, exc
                )
                if attempt == 0:
                    continue
            except Exception as exc:  # noqa: BLE001 - fall through to the deterministic fallback
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl scrape failed", exc_info=True)
                break

    # Deterministic fallback when unconfigured or request fails. The reason is
    # part of the result so the model can tell a missing key from a rejected one.
    fallback = {
        "url": url_clean,
        "status": 200,
        "title": "Simulated Page",
        "mock": True,
        "reason": reason,
    }
    if args.capture_screenshot:
        target = _screenshot_path(context, url_clean)
        target.write_bytes(FALLBACK_PNG_BYTES)
        return ToolResult.success(content=fallback, artifact_ref=str(target))
    return ToolResult.success(content=fallback)


async def browser_extract(args: BrowserExtractArgs, context: ToolContext) -> ToolResult:
    """Extract webpage content as clean markdown or html via Firecrawl or deterministic fallback."""
    api_key, base_url = _firecrawl_key(context)
    url_clean = _normalize_url(args.url)
    reason = "No FIRECRAWL_API_KEY configured in the vault or environment."

    if api_key:
        reason = "Firecrawl request failed."
        scrape_url = f"{base_url}/v1/scrape"
        body = {
            "url": url_clean,
            "formats": [args.format],
            "onlyMainContent": args.only_main_content,
        }
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(
                        scrape_url, headers=_headers(api_key), json=body
                    )
                    if res.status_code == 200:
                        data = res.json()
                        payload = data.get("data", {}) if isinstance(data, dict) else {}
                        if not isinstance(payload, dict):
                            payload = {}
                        content_text = payload.get("markdown") or payload.get("content") or f"# Content for {url_clean}\n\n..."
                        metadata = payload.get("metadata")
                        title = metadata.get("title") if isinstance(metadata, dict) else None
                        return ToolResult.success(
                            content={
                                "url": url_clean,
                                "markdown": content_text,
                                "title": title or "Page",
                                "status": 200,
                            }
                        )
                    reason = _firecrawl_error(res)
                    break
            except httpx.TransportError as exc:
                reason = (
                    "Firecrawl request failed "
                    f"({type(exc).__name__}): {exc}"
                )
                logger.warning(
                    "Firecrawl extract attempt %d failed: %s", attempt + 1, exc
                )
                if attempt == 0:
                    continue
            except Exception as exc:  # noqa: BLE001 - the fallback answers below
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl extract failed", exc_info=True)
                break

    # Deterministic fallback mock
    markdown = f"# Content for {url_clean}\n\nExtracted readable content for {url_clean}."
    return ToolResult.success(
        content={
            "url": url_clean,
            "markdown": markdown,
            "title": "Simulated Page",
            "status": 200,
            "mock": True,
            "reason": reason,
        }
    )


browser_navigate_tool = Tool.define(
    name="browser_navigate",
    description="Navigate a live cloud browser to a webpage, capture a PNG screenshot, and stream the browser window to the chat.",
    level="local",
    args_model=BrowserNavigateArgs,
    handler=browser_navigate,
)

browser_extract_tool = Tool.define(
    name="browser_extract",
    description="Extract clean structured markdown or text content from a webpage URL with main content isolation.",
    level="local",
    args_model=BrowserExtractArgs,
    handler=browser_extract,
)

BROWSER_TOOLS = (browser_navigate_tool, browser_extract_tool)

__all__ = [
    "BROWSER_TOOLS",
    "FALLBACK_PNG_BYTES",
    "SESSION_ACTIVITY_TTL_SECONDS",
    "SESSION_TTL_SECONDS",
    "BrowserExtractArgs",
    "BrowserNavigateArgs",
    "LiveSession",
    "browser_extract",
    "browser_extract_tool",
    "browser_navigate",
    "browser_navigate_tool",
    "close_browser_session",
]
