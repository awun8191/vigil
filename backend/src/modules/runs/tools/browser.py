"""Interactive CUA (Computer Use Agent) browser tools and pluggable drivers.

Supports three execution drivers behind a unified stateful session per run:

1. **Firecrawl Live Browser (``/v2/interact``)**: Cloud Playwright session with
   live-view streaming URLs (``live_view_url`` and ``interactive_live_view_url``)
   embeddable in the chat window for real-time watching and user takeover.
2. **MCP Browser Driver**: Drives a registered browser MCP server (such as
   ``@playwright/mcp``) over persistent MCP transport.
3. **Local Chromium CDP (Chrome DevTools Protocol)**: Launches a local headless
   Chromium instance driven directly over WebSocket CDP using ``websockets``,
   with full mouse, keyboard, scroll, DOM element indexing, and screenshot capture.

Every interactive action (``browser_navigate``, ``browser_snapshot``,
``browser_click``, ``browser_type``, ``browser_scroll``, ``browser_press_key``,
``browser_extract``, ``browser_cua_task``) updates the shared session state,
assigns stable ``@eN`` interactive element references with ``(x, y)`` viewport
coordinates, and writes a PNG screenshot artifact for the frontend CUA console.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config.settings import Settings, get_settings
from src.modules.runs.tools import Tool, ToolContext, ToolResult

logger = logging.getLogger("vigil.browser")

#: Valid 1x1 PNG fallback byte representation.
FALLBACK_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

#: Live browser sessions are reused across the tool calls of one run.
#: Keyed by the run delivery directory (unique per run).
_SESSIONS: dict[str, Any] = {}

#: Session lifetime caps. The chat can show the window after the run ends,
#: and the cap bounds Firecrawl browser-minute billing.
SESSION_TTL_SECONDS = 900
SESSION_ACTIVITY_TTL_SECONDS = 600

DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 800


# =============================================================================
# Tool Argument Schemas
# =============================================================================


class BrowserNavigateArgs(BaseModel):
    """Arguments for browser_navigate tool."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., min_length=1, description="Target webpage URL to navigate to and render.")
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot of the rendered page.")
    wait_for_selector: str | None = Field(
        default=None,
        description="Optional CSS selector to wait for before capturing screenshot.",
    )
    wait_until: Literal["domcontentloaded", "load", "networkidle"] = Field(
        default="domcontentloaded",
        description="Navigation readiness milestone before returning.",
    )
    driver: Literal["auto", "firecrawl", "cdp", "mcp"] | None = Field(
        default=None,
        description="Optional driver override ('firecrawl', 'cdp', 'mcp'). Defaults to user settings.",
    )


class BrowserSnapshotArgs(BaseModel):
    """Arguments for browser_snapshot tool."""

    model_config = ConfigDict(extra="ignore")

    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot of the current viewport.")
    max_elements: int = Field(default=60, ge=1, le=200, description="Maximum interactive elements to index.")


class BrowserClickArgs(BaseModel):
    """Arguments for browser_click tool."""

    model_config = ConfigDict(extra="ignore")

    selector: str | None = Field(
        default=None,
        description="Target element ref (e.g. '@e3'), CSS selector, or visible text selector.",
    )
    x: float | None = Field(default=None, ge=0, description="Viewport X coordinate in pixels.")
    y: float | None = Field(default=None, ge=0, description="Viewport Y coordinate in pixels.")
    button: Literal["left", "right", "middle"] = Field(default="left", description="Mouse button to click.")
    double_click: bool = Field(default=False, description="Whether to double-click the target.")
    wait_after_ms: int = Field(default=400, ge=0, le=15000, description="Wait time after clicking in ms.")
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot after clicking.")

    @model_validator(mode="after")
    def _require_target(self) -> "BrowserClickArgs":
        has_selector = bool(self.selector and self.selector.strip())
        has_coords = self.x is not None and self.y is not None
        if not has_selector and not has_coords:
            raise ValueError("Provide either 'selector' (e.g. '@e3' or CSS) or both 'x' and 'y' coordinates.")
        return self


class BrowserTypeArgs(BaseModel):
    """Arguments for browser_type tool."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(..., description="Text to type into the input or focused element.")
    selector: str | None = Field(
        default=None,
        description="Target input ref (e.g. '@e2') or CSS selector. Uses current focus if omitted.",
    )
    x: float | None = Field(default=None, ge=0, description="Optional viewport X coordinate to click before typing.")
    y: float | None = Field(default=None, ge=0, description="Optional viewport Y coordinate to click before typing.")
    clear_first: bool = Field(default=True, description="Clear existing input text before typing.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")
    wait_after_ms: int = Field(default=300, ge=0, le=15000, description="Wait time after typing in ms.")
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot after typing.")


class BrowserScrollArgs(BaseModel):
    """Arguments for browser_scroll tool."""

    model_config = ConfigDict(extra="ignore")

    direction: Literal["down", "up", "left", "right", "top", "bottom"] = Field(
        default="down",
        description="Scroll direction.",
    )
    amount: int = Field(default=500, ge=1, le=10000, description="Scroll distance in pixels.")
    selector: str | None = Field(
        default=None,
        description="Optional element ref or CSS selector to scroll instead of the document viewport.",
    )
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot after scrolling.")


class BrowserPressKeyArgs(BaseModel):
    """Arguments for browser_press_key tool."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(
        ...,
        min_length=1,
        description="Keyboard key or chord to press (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown', 'Control+a').",
    )
    wait_after_ms: int = Field(default=300, ge=0, le=15000, description="Wait time after key press in ms.")
    capture_screenshot: bool = Field(default=True, description="Capture a PNG screenshot after key press.")


class BrowserExtractArgs(BaseModel):
    """Arguments for browser_extract tool."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = Field(
        default=None,
        min_length=1,
        description="Webpage URL to extract content from. Omit to extract from the active live browser page.",
    )
    selector: str | None = Field(
        default=None,
        description="Optional CSS selector or '@eN' ref to scope DOM extraction on the active page.",
    )
    format: str = Field(default="markdown", description="Extraction format, default: markdown.")
    only_main_content: bool = Field(
        default=True,
        description="Whether to isolate main content, stripping navigation and footers.",
    )


class BrowserCUATaskArgs(BaseModel):
    """Arguments for browser_cua_task autonomous vision-action loop."""

    model_config = ConfigDict(extra="ignore")

    goal: str = Field(..., min_length=1, description="End-to-end UI interaction goal to accomplish in the browser.")
    start_url: str | None = Field(default=None, description="Optional starting URL to open before acting.")
    max_steps: int = Field(default=8, ge=1, le=20, description="Maximum vision-action steps before stopping.")


# =============================================================================
# Session Models
# =============================================================================


@dataclass
class LiveSession:
    """One live Firecrawl browser session."""

    id: str
    live_view_url: str | None
    interactive_live_view_url: str | None
    expires_at: datetime | None
    current_url: str = ""
    driver: str = "firecrawl"

    def is_expired(self) -> bool:
        """Return True when the session lifetime cap has passed."""
        return self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc)


@dataclass
class LocalCDPSession:
    """One local headless Chromium session driven over WebSocket CDP."""

    id: str
    proc: asyncio.subprocess.Process
    ws: Any
    profile_dir: Path
    current_url: str = "about:blank"
    driver: str = "cdp"
    viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    viewport_height: int = DEFAULT_VIEWPORT_HEIGHT
    _cmd_id: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_expired(self) -> bool:
        return self.proc.returncode is not None

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one CDP JSON-RPC command and wait for its matching response."""
        async with self._lock:
            self._cmd_id += 1
            msg_id = self._cmd_id
            payload = {"id": msg_id, "method": method, "params": params or {}}
            await self.ws.send(json.dumps(payload))
            while True:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
                data = json.loads(raw)
                if data.get("id") != msg_id:
                    continue
                if "error" in data:
                    raise RuntimeError(f"CDP {method} failed: {data['error']}")
                result = data.get("result")
                return result if isinstance(result, dict) else {}

    async def eval_js(self, expression: str) -> Any:
        """Evaluate JavaScript in the page and return the JSON value."""
        res = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        exc = res.get("exceptionDetails")
        if exc:
            text = exc.get("text") or str(exc)
            raise RuntimeError(f"Browser script error: {text}")
        result_obj = res.get("result")
        if isinstance(result_obj, dict):
            return result_obj.get("value")
        return None

    async def capture_screenshot_b64(self) -> str | None:
        """Capture a viewport PNG screenshot as base64."""
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        data = res.get("data")
        return data if isinstance(data, str) and data else None

    async def close(self) -> None:
        """Close the CDP WebSocket, terminate Chromium, and remove the temp profile."""
        with contextlib.suppress(Exception):
            await self.ws.close()
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                with contextlib.suppress(Exception):
                    await self.proc.wait()
        shutil.rmtree(self.profile_dir, ignore_errors=True)


@dataclass
class MCPBrowserSession:
    """One browser session backed by a registered MCP server (e.g. Playwright MCP)."""

    id: str
    server_name: str
    tool_map: dict[str, str]
    current_url: str = ""
    driver: str = "mcp"

    def is_expired(self) -> bool:
        return False


# =============================================================================
# Shared Browser DOM Scripts & Helpers
# =============================================================================


_INDEX_ELEMENTS_JS = """
((maxElements) => {
  const sel = 'a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="textbox"], [role="checkbox"], [role="radio"], [role="combobox"], [role="menuitem"], [role="tab"], [contenteditable="true"], [tabindex]';
  const nodes = Array.from(document.querySelectorAll(sel));
  const elements = [];
  let idx = 0;
  for (const el of nodes) {
    if (elements.length >= (maxElements || 60)) break;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) continue;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
    idx += 1;
    const vid = 'e' + idx;
    el.setAttribute('data-vigil-id', vid);
    const label = (
      el.innerText ||
      el.value ||
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      ''
    ).replace(/\\s+/g, ' ').trim().slice(0, 80);
    elements.push({
      id: '@' + vid,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || undefined,
      role: el.getAttribute('role') || undefined,
      text: label,
      href: el.getAttribute('href') || undefined,
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      selector: '[data-vigil-id="' + vid + '"]',
    });
  }
  const textExcerpt = (document.body ? (document.body.innerText || '') : '').replace(/\\s+/g, ' ').trim().slice(0, 1500);
  return {
    title: document.title || '',
    url: window.location.href,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    elements,
    text_excerpt: textExcerpt,
  };
})
"""

_EXTRACT_DOM_JS = """
((selector, onlyMain, fmt) => {
  const root = selector
    ? document.querySelector(selector)
    : (onlyMain ? (document.querySelector('main, article, [role="main"]') || document.body) : document.body);
  if (!root) {
    return { title: document.title || '', url: window.location.href, content: '' };
  }
  if (fmt === 'html') {
    return { title: document.title || '', url: window.location.href, content: root.innerHTML || '' };
  }
  const lines = [];
  const headings = new Set(['H1', 'H2', 'H3', 'H4']);
  const walk = (node) => {
    if (node.nodeType === 3) {
      const t = (node.textContent || '').replace(/\\s+/g, ' ').trim();
      if (t) lines.push(t);
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.tagName;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG'].includes(tag)) return;
    if (onlyMain && ['NAV', 'FOOTER', 'ASIDE'].includes(tag)) return;
    if (headings.has(tag)) {
      const prefix = '#'.repeat(parseInt(tag[1], 10));
      const t = (node.innerText || '').replace(/\\s+/g, ' ').trim();
      if (t) lines.push('\\n' + prefix + ' ' + t + '\\n');
      return;
    }
    if (tag === 'A' && node.getAttribute('href')) {
      const t = (node.innerText || '').replace(/\\s+/g, ' ').trim();
      const href = node.href || node.getAttribute('href');
      if (t) lines.push('[' + t + '](' + href + ')');
      return;
    }
    if (tag === 'LI') {
      const t = (node.innerText || '').replace(/\\s+/g, ' ').trim();
      if (t) lines.push('- ' + t);
      return;
    }
    for (const child of node.childNodes) walk(child);
    if (['P', 'DIV', 'SECTION', 'TR', 'BR'].includes(tag)) lines.push('\\n');
  };
  walk(root);
  const raw = lines.join(' ').replace(/ +\\n/g, '\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
  return {
    title: document.title || '',
    url: window.location.href,
    content: raw || (root.innerText || '').trim(),
  };
})
"""


def _normalize_url(url: str) -> str:
    """Return an absolute http(s) or data URL."""
    clean = url.strip()
    if not (
        clean.startswith("http://")
        or clean.startswith("https://")
        or clean.startswith("data:")
    ):
        clean = f"https://{clean}"
    return clean


def _resolve_selector(selector: str | None) -> str | None:
    """Convert a '@eN' element ref to its DOM attribute selector."""
    if not selector:
        return None
    stripped = selector.strip()
    if re.fullmatch(r"@e\d+", stripped):
        return f'[data-vigil-id="{stripped[1:]}"]'
    return stripped


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


def _session_fields(session: Any | None) -> dict[str, str]:
    """Return the live view fields a tool result carries for the chat panel."""
    if session is None or (hasattr(session, "is_expired") and session.is_expired()):
        return {}
    fields: dict[str, str] = {
        "browser_session_id": str(session.id),
        "driver": str(getattr(session, "driver", "firecrawl")),
    }
    if isinstance(session, LiveSession):
        if session.live_view_url:
            fields["live_view_url"] = session.live_view_url
        if session.interactive_live_view_url:
            fields["interactive_live_view_url"] = session.interactive_live_view_url
        if session.expires_at is not None:
            fields["session_expires_at"] = session.expires_at.isoformat()
    return fields


def _active_session(context: ToolContext) -> Any | None:
    """Return the live session of this run, if it has not expired."""
    session = _SESSIONS.get(str(context.delivery_dir))
    if session is None or (hasattr(session, "is_expired") and session.is_expired()):
        return None
    return session


async def _delete_session(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], session_id: str
) -> None:
    """Close one Firecrawl session, best effort."""
    try:
        await client.delete(f"{base_url}/v2/interact/{session_id}", headers=headers)
    except Exception:  # noqa: BLE001 - cleanup never masks the tool result
        pass


async def close_browser_session(
    delivery_dir: str | Path,
    api_key: str | None = None,
    base_url: str | None = None,
) -> None:
    """Close the live session of one run and forget it.

    Cleans up Firecrawl cloud sessions, local Chromium CDP subprocesses,
    or MCP browser session handles.
    """
    session = _SESSIONS.pop(str(delivery_dir), None)
    if session is None:
        return
    if isinstance(session, LocalCDPSession):
        await session.close()
        return
    if isinstance(session, LiveSession):
        if not api_key:
            return
        base = (base_url or get_settings().FIRECRAWL_BASE_URL).rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _delete_session(client, base, _headers(api_key), session.id)


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


def _screenshot_path(context: ToolContext, tag: str) -> Path:
    """Return the delivery path for one page screenshot."""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    tag_hash = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8]
    return context.delivery_dir / f"screenshot_{ts}_{tag_hash}.png"


def _write_screenshot(
    context: ToolContext,
    tag: str,
    b64_data: Any,
    *,
    fallback_to_1x1: bool = True,
) -> str | None:
    """Decode base64 screenshot and save into delivery_dir."""
    png_bytes = _decode_screenshot(b64_data)
    if png_bytes is None:
        if not fallback_to_1x1:
            return None
        png_bytes = FALLBACK_PNG_BYTES
    context.delivery_dir.mkdir(parents=True, exist_ok=True)
    target = _screenshot_path(context, tag)
    target.write_bytes(png_bytes)
    return str(target)


# =============================================================================
# Firecrawl Live Driver Helpers
# =============================================================================


async def _ensure_firecrawl_session(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    key: str,
) -> LiveSession | None:
    """Return the reusable Firecrawl session of this run, or create one."""
    for stale_key, stale in list(_SESSIONS.items()):
        if hasattr(stale, "is_expired") and stale.is_expired():
            _SESSIONS.pop(stale_key, None)

    existing = _SESSIONS.get(key)
    if isinstance(existing, LiveSession) and not existing.is_expired():
        return existing
    if existing is not None:
        _SESSIONS.pop(key, None)
        if isinstance(existing, LiveSession):
            await _delete_session(client, base_url, headers, existing.id)
        elif isinstance(existing, LocalCDPSession):
            await existing.close()

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


async def _execute_firecrawl(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    code: str,
    *,
    language: str = "node",
    timeout: int = 45,
) -> dict[str, Any] | None:
    """Run Playwright code in a live Firecrawl session and return the response body."""
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


def _navigation_code(
    url: str,
    wait_for_selector: str | None,
    screenshot: bool,
    wait_until: str = "domcontentloaded",
) -> str:
    """Build Playwright code to navigate, index interactive elements, and screenshot."""
    lines = [
        f"await page.goto({json.dumps(url)}, {{ waitUntil: {json.dumps(wait_until)}, timeout: 30000 }});"
    ]
    if wait_for_selector:
        lines.append(
            f"await page.waitForSelector({json.dumps(wait_for_selector)}, "
            "{ timeout: 15000 }).catch(() => {});"
        )
    lines.append(f"const snap = await page.evaluate({_INDEX_ELEMENTS_JS}, 60);")
    if screenshot:
        lines.append("const shot = (await page.screenshot({ type: 'png' })).toString('base64');")
        lines.append("JSON.stringify({ ...snap, screenshot: shot });")
    else:
        lines.append("JSON.stringify(snap);")
    return "\n".join(lines)


def _firecrawl_action_code(action_lines: list[str], screenshot: bool, max_elements: int = 60) -> str:
    """Wrap Playwright action lines with element re-indexing and optional screenshot."""
    lines = list(action_lines)
    lines.append(f"const snap = await page.evaluate({_INDEX_ELEMENTS_JS}, {int(max_elements)});")
    if screenshot:
        lines.append("const shot = (await page.screenshot({ type: 'png' })).toString('base64');")
        lines.append("JSON.stringify({ ...snap, screenshot: shot });")
    else:
        lines.append("JSON.stringify(snap);")
    return "\n".join(lines)


def _scrape_screenshot(payload: dict[str, Any]) -> tuple[str, Any]:
    """Return the title and raw screenshot value from a v1 scrape response."""
    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    metadata = data.get("metadata")
    title = metadata.get("title") if isinstance(metadata, dict) else None
    return str(title or ""), data.get("screenshot")


async def _run_firecrawl_code(
    session: LiveSession,
    api_key: str,
    base_url: str,
    code: str,
) -> dict[str, Any] | None:
    """Execute Playwright code on an existing LiveSession and decode its JSON payload."""
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=60.0) as client:
        payload = await _execute_firecrawl(client, base_url, headers, session.id, code)
        return _execution_payload(payload) if payload is not None else None


async def _navigate_live(
    url: str,
    args: BrowserNavigateArgs,
    context: ToolContext,
    api_key: str,
    base_url: str,
) -> ToolResult | None:
    """Drive the live Firecrawl session to the URL and return the result, or None on failure."""
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=60.0) as client:
        session = await _ensure_firecrawl_session(
            client, base_url, headers, str(context.delivery_dir)
        )
        if session is None:
            return None
        payload = await _execute_firecrawl(
            client,
            base_url,
            headers,
            session.id,
            _navigation_code(
                url,
                args.wait_for_selector,
                args.capture_screenshot,
                wait_until=args.wait_until,
            ),
        )
        page = _execution_payload(payload) if payload is not None else None
        if page is None:
            return None

        title = str(page.get("title") or url)
        resolved_url = str(page.get("url") or url)
        session.current_url = resolved_url
        content: dict[str, Any] = {
            "url": resolved_url,
            "title": title,
            "status": 200,
            "action": "navigate",
            "elements": page.get("elements") or [],
            "text_excerpt": page.get("text_excerpt") or "",
            "viewport": page.get("viewport") or {
                "width": DEFAULT_VIEWPORT_WIDTH,
                "height": DEFAULT_VIEWPORT_HEIGHT,
            },
            **_session_fields(session),
        }
        artifact_ref: str | None = None
        if args.capture_screenshot:
            artifact_ref = _write_screenshot(context, resolved_url, page.get("screenshot"))
        return ToolResult.success(content=content, artifact_ref=artifact_ref)


# =============================================================================
# Local Chromium CDP Driver
# =============================================================================


def _find_chromium_binary(settings: Settings) -> str | None:
    """Return an executable Chromium/Chrome binary path if installed."""
    if settings.CHROMIUM_BINARY:
        candidate = Path(settings.CHROMIUM_BINARY)
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(settings.CHROMIUM_BINARY)
        if resolved:
            return resolved
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def _wait_for_cdp_port(profile_dir: Path, proc: asyncio.subprocess.Process) -> int:
    """Wait for Chromium's DevToolsActivePort file or stderr announcement."""
    port_file = profile_dir / "DevToolsActivePort"
    deadline = asyncio.get_running_loop().time() + 10.0
    while asyncio.get_running_loop().time() < deadline:
        if proc.returncode is not None:
            err = ""
            if proc.stderr is not None:
                with contextlib.suppress(Exception):
                    err = (await proc.stderr.read()).decode("utf-8", "replace")[:400]
            raise RuntimeError(f"Chromium exited early (code {proc.returncode}): {err}")
        if port_file.is_file():
            lines = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines and lines[0].strip().isdigit():
                return int(lines[0].strip())
        await asyncio.sleep(0.05)
    raise TimeoutError("Chromium did not publish DevToolsActivePort within 10s")


async def _ensure_cdp_session(context: ToolContext, settings: Settings) -> LocalCDPSession:
    """Return the reusable LocalCDPSession for this run, or launch Chromium."""
    key = str(context.delivery_dir)
    existing = _SESSIONS.get(key)
    if isinstance(existing, LocalCDPSession) and not existing.is_expired():
        return existing
    if existing is not None:
        _SESSIONS.pop(key, None)
        if isinstance(existing, LocalCDPSession):
            await existing.close()

    binary = _find_chromium_binary(settings)
    if not binary:
        raise RuntimeError("No Chromium/Chrome binary found on PATH for local CDP CUA driver.")

    import websockets

    profile_dir = Path(tempfile.mkdtemp(prefix="vigil-cdp-"))
    width = int(getattr(settings, "BROWSER_VIEWPORT_WIDTH", DEFAULT_VIEWPORT_WIDTH))
    height = int(getattr(settings, "BROWSER_VIEWPORT_HEIGHT", DEFAULT_VIEWPORT_HEIGHT))
    headless = bool(getattr(settings, "BROWSER_HEADLESS", True))

    argv = [
        binary,
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
    ]
    if headless:
        argv.append("--headless=new")
    argv.append("about:blank")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        port = await _wait_for_cdp_port(profile_dir, proc)
        async with httpx.AsyncClient(timeout=5.0) as client:
            targets_resp = await client.get(f"http://127.0.0.1:{port}/json/list")
            targets = targets_resp.json()
        page_ws = None
        for t in targets:
            if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                page_ws = t["webSocketDebuggerUrl"]
                break
        if not page_ws:
            raise RuntimeError("Chromium exposed no page WebSocket debugger target.")

        ws = await websockets.connect(page_ws, max_size=25 * 1024 * 1024)
        session = LocalCDPSession(
            id=f"cdp-{port}",
            proc=proc,
            ws=ws,
            profile_dir=profile_dir,
            viewport_width=width,
            viewport_height=height,
        )
        await session.send("Page.enable")
        await session.send("Runtime.enable")
        await session.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        _SESSIONS[key] = session
        return session
    except Exception:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise


async def _cdp_wait_ready(session: LocalCDPSession, wait_for_selector: str | None = None) -> None:
    """Wait briefly for document readyState and optional CSS selector."""
    for _ in range(60):
        state = await session.eval_js("document.readyState")
        if state in ("interactive", "complete"):
            break
        await asyncio.sleep(0.1)
    if wait_for_selector:
        sel_json = json.dumps(wait_for_selector)
        for _ in range(40):
            found = await session.eval_js(f"Boolean(document.querySelector({sel_json}))")
            if found:
                break
            await asyncio.sleep(0.1)


async def _cdp_snapshot_payload(
    session: LocalCDPSession,
    context: ToolContext,
    *,
    tag: str,
    capture_screenshot: bool,
    max_elements: int = 60,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    """Run element indexing + optional screenshot on LocalCDPSession."""
    snap = await session.eval_js(f"({_INDEX_ELEMENTS_JS})({int(max_elements)})")
    if not isinstance(snap, dict):
        snap = {}
    resolved_url = str(snap.get("url") or session.current_url or "about:blank")
    session.current_url = resolved_url
    content: dict[str, Any] = {
        "url": resolved_url,
        "title": str(snap.get("title") or resolved_url),
        "status": 200,
        "elements": snap.get("elements") or [],
        "text_excerpt": snap.get("text_excerpt") or "",
        "viewport": snap.get("viewport") or {
            "width": session.viewport_width,
            "height": session.viewport_height,
        },
        **_session_fields(session),
        **(extra or {}),
    }
    artifact_ref: str | None = None
    if capture_screenshot:
        b64 = await session.capture_screenshot_b64()
        artifact_ref = _write_screenshot(context, tag, b64)
    return ToolResult.success(content=content, artifact_ref=artifact_ref)


def _cdp_key_spec(key: str) -> tuple[str, int, list[str]]:
    """Parse a key or chord like 'Control+a' into (base_key, vk_code, modifiers)."""
    parts = [p.strip() for p in key.split("+") if p.strip()]
    modifiers: list[str] = []
    base = parts[-1] if parts else key
    for mod in parts[:-1]:
        low = mod.lower()
        if low in ("ctrl", "control"):
            modifiers.append("Control")
        elif low in ("alt", "option"):
            modifiers.append("Alt")
        elif low in ("shift",):
            modifiers.append("Shift")
        elif low in ("meta", "cmd", "command", "super"):
            modifiers.append("Meta")

    vk_map = {
        "Enter": 13,
        "Tab": 9,
        "Escape": 27,
        "Backspace": 8,
        "Delete": 46,
        "ArrowLeft": 37,
        "ArrowUp": 38,
        "ArrowRight": 39,
        "ArrowDown": 40,
        "Home": 36,
        "End": 35,
        "PageUp": 33,
        "PageDown": 34,
        "Space": 32,
        " ": 32,
    }
    vk = vk_map.get(base, ord(base.upper()[0]) if len(base) == 1 else 0)
    return base, vk, modifiers


def _cdp_modifier_mask(modifiers: list[str]) -> int:
    mask = 0
    if "Alt" in modifiers:
        mask |= 1
    if "Control" in modifiers:
        mask |= 2
    if "Meta" in modifiers:
        mask |= 4
    if "Shift" in modifiers:
        mask |= 8
    return mask


# =============================================================================
# MCP Browser Driver Helpers
# =============================================================================


def _find_mcp_browser_server() -> tuple[str, dict[str, str]] | None:
    """Return (server_name, tool_map) if a registered MCP server exposes browser tools."""
    from src.modules.connectors.service import ConnectorsService

    settings = get_settings()
    preferred = (getattr(settings, "BROWSER_MCP_SERVER", "") or "").strip()
    candidates = list(ConnectorsService._mcp_servers.values())
    if preferred:
        candidates.sort(key=lambda s: 0 if s.name == preferred else 1)

    for server in candidates:
        if not getattr(server, "enabled", True):
            continue
        tools = {
            t.name: t
            for (sid, _), t in ConnectorsService._mcp_tools.items()
            if sid == server.id
        }
        tool_map: dict[str, str] = {}
        for action in ("navigate", "click", "type", "snapshot", "press_key", "take_screenshot", "scroll"):
            aliased = f"{server.name}__browser_{action}"
            direct = f"browser_{action}"
            if aliased in tools:
                tool_map[action] = aliased
            elif direct in tools:
                tool_map[action] = direct
        if "navigate" in tool_map:
            return server.name, tool_map
    return None


async def _call_mcp_browser(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> tuple[str, bool]:
    """Call an MCP browser tool through MCPHostClient with persistent session pooling."""
    from src.integrations.mcp.mcp import MCPHostClient

    host = MCPHostClient(get_settings(), persistent=True)
    host.start_session(str(context.delivery_dir), vault_secrets=dict(context.secrets))
    res = await host.execute_tool(server_name, tool_name, arguments)
    return str(res.output), bool(res.is_error)


# =============================================================================
# Driver Resolution
# =============================================================================


def _disabled_drivers(context: ToolContext) -> set[str]:
    """Return the set of browser drivers explicitly disabled in user settings."""
    raw = (context.secrets.get("DISABLED_BROWSER_DRIVERS") or "").strip()
    if not raw:
        return set()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(item).strip().lower() for item in parsed if str(item).strip()}
        except Exception:  # noqa: BLE001
            pass
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _resolve_driver(
    context: ToolContext,
    requested_driver: str | None = None,
) -> tuple[str, str | None, str]:
    """Return (driver_name, firecrawl_key, firecrawl_base_url).

    driver_name is one of: 'firecrawl', 'mcp', 'cdp', 'mock', 'disabled', or 'blocked:<name>'.
    """
    settings = get_settings()
    api_key, base_url = _firecrawl_key(context)
    disabled = _disabled_drivers(context)
    pref = (
        requested_driver
        or context.secrets.get("BROWSER_DRIVER")
        or getattr(settings, "BROWSER_DRIVER", "auto")
        or "auto"
    )
    if pref == "disabled":
        return "disabled", api_key, base_url
    if pref == "mock":
        return "mock", api_key, base_url
    if pref in ("firecrawl", "cdp", "mcp"):
        if pref in disabled:
            return f"blocked:{pref}", api_key, base_url
        if pref == "firecrawl":
            return ("firecrawl" if api_key else "mock"), api_key, base_url
        return pref, api_key, base_url

    # "auto": Firecrawl when enabled & key present -> connected browser MCP -> local CDP -> mock
    if api_key and "firecrawl" not in disabled:
        return "firecrawl", api_key, base_url
    if "mcp" not in disabled and _find_mcp_browser_server() is not None:
        return "mcp", None, base_url
    if (
        "cdp" not in disabled
        and settings.APP_ENV != "test"
        and context.run_id is not None
        and _find_chromium_binary(settings)
    ):
        return "cdp", None, base_url
    return "mock", None, base_url


# =============================================================================
# Core Browser Tools
# =============================================================================


async def browser_navigate(args: BrowserNavigateArgs, context: ToolContext) -> ToolResult:
    """Open a URL in the live browser and capture interactive elements + screenshot."""
    url_clean = _normalize_url(args.url)
    context.delivery_dir.mkdir(parents=True, exist_ok=True)
    driver, api_key, base_url = _resolve_driver(context, args.driver)
    if driver == "disabled":
        return ToolResult.failure(
            "Browser automation is disabled in Settings → Connectors."
        )
    if driver.startswith("blocked:"):
        blocked_name = driver.split(":", 1)[1]
        return ToolResult.failure(
            f"Browser driver {blocked_name!r} is disabled in Settings → Connectors."
        )
    reason = "No FIRECRAWL_API_KEY configured in the vault or environment."

    if driver == "firecrawl" and api_key:
        reason = "Firecrawl request failed."
        try:
            live = await _navigate_live(url_clean, args, context, api_key, base_url)
            if live is not None:
                return live
        except Exception as exc:  # noqa: BLE001 - fall back to v1 scrape
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
                            "action": "navigate",
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
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl scrape attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    continue
            except Exception as exc:  # noqa: BLE001
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl scrape failed", exc_info=True)
                break

    elif driver == "cdp":
        try:
            session = await _ensure_cdp_session(context, get_settings())
            await session.send("Page.navigate", {"url": url_clean})
            await _cdp_wait_ready(session, args.wait_for_selector)
            return await _cdp_snapshot_payload(
                session,
                context,
                tag=url_clean,
                capture_screenshot=args.capture_screenshot,
                extra={"action": "navigate"},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Local Chromium CDP navigation failed: {exc}")

    elif driver == "mcp":
        found = _find_mcp_browser_server()
        if found is None:
            return ToolResult.failure("No browser MCP server is currently registered.")
        srv_name, tool_map = found
        output, is_err = await _call_mcp_browser(
            srv_name, tool_map["navigate"], {"url": url_clean}, context
        )
        if is_err:
            return ToolResult.failure(f"MCP browser navigate failed: {output}")
        session = MCPBrowserSession(
            id=f"mcp-{srv_name}",
            server_name=srv_name,
            tool_map=tool_map,
            current_url=url_clean,
        )
        _SESSIONS[str(context.delivery_dir)] = session
        artifact_ref: str | None = None
        if args.capture_screenshot and "take_screenshot" in tool_map:
            shot_out, shot_err = await _call_mcp_browser(
                srv_name, tool_map["take_screenshot"], {}, context
            )
            if not shot_err:
                artifact_ref = _write_screenshot(context, url_clean, shot_out, fallback_to_1x1=False)
        return ToolResult.success(
            content={
                "url": url_clean,
                "title": url_clean,
                "status": 200,
                "action": "navigate",
                "output": output[:2000],
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    # Deterministic fallback when unconfigured or Firecrawl request fails.
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


async def browser_snapshot(args: BrowserSnapshotArgs, context: ToolContext) -> ToolResult:
    """Capture the current viewport screenshot and interactive element map."""
    session = _active_session(context)
    if session is None:
        return ToolResult.failure(
            "No active browser session. Call browser_navigate(url=...) first."
        )

    if isinstance(session, LocalCDPSession):
        return await _cdp_snapshot_payload(
            session,
            context,
            tag=f"snapshot-{session.current_url}",
            capture_screenshot=args.capture_screenshot,
            max_elements=args.max_elements,
            extra={"action": "snapshot"},
        )

    if isinstance(session, LiveSession):
        api_key, base_url = _firecrawl_key(context)
        if not api_key:
            return ToolResult.failure("FIRECRAWL_API_KEY is required for live browser snapshot.")
        code = _firecrawl_action_code([], args.capture_screenshot, max_elements=args.max_elements)
        page = await _run_firecrawl_code(session, api_key, base_url, code)
        if page is None:
            return ToolResult.failure("Firecrawl live snapshot failed.")
        resolved_url = str(page.get("url") or session.current_url)
        session.current_url = resolved_url
        artifact_ref = (
            _write_screenshot(context, f"snapshot-{resolved_url}", page.get("screenshot"))
            if args.capture_screenshot
            else None
        )
        return ToolResult.success(
            content={
                "url": resolved_url,
                "title": str(page.get("title") or resolved_url),
                "status": 200,
                "action": "snapshot",
                "elements": page.get("elements") or [],
                "text_excerpt": page.get("text_excerpt") or "",
                "viewport": page.get("viewport") or {},
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    if isinstance(session, MCPBrowserSession):
        snap_tool = session.tool_map.get("snapshot")
        if not snap_tool:
            return ToolResult.failure("MCP browser server does not expose browser_snapshot.")
        output, is_err = await _call_mcp_browser(session.server_name, snap_tool, {}, context)
        if is_err:
            return ToolResult.failure(output)
        return ToolResult.success(
            content={
                "url": session.current_url,
                "action": "snapshot",
                "snapshot": output,
                **_session_fields(session),
            }
        )

    return ToolResult.failure("Unsupported browser session state.")


async def browser_click(args: BrowserClickArgs, context: ToolContext) -> ToolResult:
    """Click an element by '@eN' ref, CSS selector, or viewport (x, y) coordinates."""
    session = _active_session(context)
    if session is None:
        return ToolResult.failure(
            "No active browser session. Call browser_navigate(url=...) first."
        )
    resolved_sel = _resolve_selector(args.selector)

    if isinstance(session, LocalCDPSession):
        click_x = args.x
        click_y = args.y
        if click_x is None or click_y is None:
            assert resolved_sel is not None
            sel_json = json.dumps(resolved_sel)
            pos = await session.eval_js(
                f"""(() => {{
                  const el = document.querySelector({sel_json});
                  if (!el) return null;
                  el.scrollIntoView({{ block: 'center', inline: 'center' }});
                  const r = el.getBoundingClientRect();
                  return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
                }})()"""
            )
            if not isinstance(pos, dict):
                return ToolResult.failure(f"Element not found for selector: {args.selector!r}")
            click_x = float(pos["x"])
            click_y = float(pos["y"])

        click_count = 2 if args.double_click else 1
        for event_type in ("mousePressed", "mouseReleased"):
            await session.send(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": click_x,
                    "y": click_y,
                    "button": args.button,
                    "clickCount": click_count,
                },
            )
        if args.wait_after_ms > 0:
            await asyncio.sleep(args.wait_after_ms / 1000.0)
        await _cdp_wait_ready(session)
        return await _cdp_snapshot_payload(
            session,
            context,
            tag=f"click-{click_x}-{click_y}",
            capture_screenshot=args.capture_screenshot,
            extra={
                "action": "click",
                "target": args.selector or f"({click_x}, {click_y})",
                "coordinates": {"x": click_x, "y": click_y},
            },
        )

    if isinstance(session, LiveSession):
        api_key, base_url = _firecrawl_key(context)
        if not api_key:
            return ToolResult.failure("FIRECRAWL_API_KEY is required for live browser click.")
        actions: list[str] = []
        if resolved_sel:
            sel_json = json.dumps(resolved_sel)
            if args.double_click:
                actions.append(
                    f"await page.locator({sel_json}).first().dblclick({{ button: {json.dumps(args.button)}, timeout: 10000 }});"
                )
            else:
                actions.append(
                    f"await page.locator({sel_json}).first().click({{ button: {json.dumps(args.button)}, timeout: 10000 }});"
                )
        else:
            assert args.x is not None and args.y is not None
            if args.double_click:
                actions.append(
                    f"await page.mouse.dblclick({float(args.x)}, {float(args.y)}, {{ button: {json.dumps(args.button)} }});"
                )
            else:
                actions.append(
                    f"await page.mouse.click({float(args.x)}, {float(args.y)}, {{ button: {json.dumps(args.button)} }});"
                )
        if args.wait_after_ms > 0:
            actions.append(f"await page.waitForTimeout({int(args.wait_after_ms)});")

        page = await _run_firecrawl_code(
            session,
            api_key,
            base_url,
            _firecrawl_action_code(actions, args.capture_screenshot),
        )
        if page is None:
            return ToolResult.failure(
                f"Firecrawl browser_click failed on {args.selector or (args.x, args.y)!r}."
            )
        resolved_url = str(page.get("url") or session.current_url)
        session.current_url = resolved_url
        artifact_ref = (
            _write_screenshot(context, f"click-{resolved_url}", page.get("screenshot"))
            if args.capture_screenshot
            else None
        )
        return ToolResult.success(
            content={
                "url": resolved_url,
                "title": str(page.get("title") or resolved_url),
                "status": 200,
                "action": "click",
                "target": args.selector or f"({args.x}, {args.y})",
                "elements": page.get("elements") or [],
                "text_excerpt": page.get("text_excerpt") or "",
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    if isinstance(session, MCPBrowserSession):
        click_tool = session.tool_map.get("click")
        if not click_tool:
            return ToolResult.failure("MCP browser server does not expose browser_click.")
        payload: dict[str, Any] = {}
        if args.selector:
            payload["element"] = args.selector
            payload["ref"] = args.selector.lstrip("@")
        if args.x is not None and args.y is not None:
            payload["x"] = args.x
            payload["y"] = args.y
        output, is_err = await _call_mcp_browser(session.server_name, click_tool, payload, context)
        if is_err:
            return ToolResult.failure(output)
        return ToolResult.success(
            content={
                "url": session.current_url,
                "action": "click",
                "target": args.selector or f"({args.x}, {args.y})",
                "output": output[:2000],
                **_session_fields(session),
            }
        )

    return ToolResult.failure("Unsupported browser session state.")


async def browser_type(args: BrowserTypeArgs, context: ToolContext) -> ToolResult:
    """Focus a target input (by ref, selector, or coordinates) and type text."""
    session = _active_session(context)
    if session is None:
        return ToolResult.failure(
            "No active browser session. Call browser_navigate(url=...) first."
        )
    resolved_sel = _resolve_selector(args.selector)

    if isinstance(session, LocalCDPSession):
        if resolved_sel:
            sel_json = json.dumps(resolved_sel)
            pos = await session.eval_js(
                f"""(() => {{
                  const el = document.querySelector({sel_json});
                  if (!el) return null;
                  el.scrollIntoView({{ block: 'center', inline: 'center' }});
                  el.focus();
                  const r = el.getBoundingClientRect();
                  return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
                }})()"""
            )
            if not isinstance(pos, dict):
                return ToolResult.failure(f"Input element not found: {args.selector!r}")
            await session.send(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": float(pos["x"]), "y": float(pos["y"]), "button": "left", "clickCount": 1},
            )
            await session.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": float(pos["x"]), "y": float(pos["y"]), "button": "left", "clickCount": 1},
            )
        elif args.x is not None and args.y is not None:
            await session.send(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": float(args.x), "y": float(args.y), "button": "left", "clickCount": 1},
            )
            await session.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": float(args.x), "y": float(args.y), "button": "left", "clickCount": 1},
            )

        if args.clear_first:
            await session.eval_js(
                """(() => {
                  const el = document.activeElement;
                  if (el && ('value' in el)) { el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true })); }
                })()"""
            )

        await session.send("Input.insertText", {"text": args.text})
        if args.press_enter:
            for ev_type in ("rawKeyDown", "keyUp"):
                await session.send(
                    "Input.dispatchKeyEvent",
                    {"type": ev_type, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
                )
        if args.wait_after_ms > 0:
            await asyncio.sleep(args.wait_after_ms / 1000.0)
        await _cdp_wait_ready(session)
        return await _cdp_snapshot_payload(
            session,
            context,
            tag=f"type-{args.selector or 'focus'}",
            capture_screenshot=args.capture_screenshot,
            extra={
                "action": "type",
                "target": args.selector or (f"({args.x}, {args.y})" if args.x is not None else "focused"),
                "typed_chars": len(args.text),
            },
        )

    if isinstance(session, LiveSession):
        api_key, base_url = _firecrawl_key(context)
        if not api_key:
            return ToolResult.failure("FIRECRAWL_API_KEY is required for live browser type.")
        actions: list[str] = []
        if resolved_sel:
            sel_json = json.dumps(resolved_sel)
            if args.clear_first:
                actions.append(f"await page.locator({sel_json}).first().fill({json.dumps(args.text)});")
            else:
                actions.append(f"await page.locator({sel_json}).first().click();")
                actions.append(f"await page.keyboard.type({json.dumps(args.text)});")
        else:
            if args.x is not None and args.y is not None:
                actions.append(f"await page.mouse.click({float(args.x)}, {float(args.y)});")
            if args.clear_first:
                actions.append("await page.keyboard.press('Control+a').catch(() => {});")
                actions.append("await page.keyboard.press('Backspace').catch(() => {});")
            actions.append(f"await page.keyboard.type({json.dumps(args.text)});")
        if args.press_enter:
            actions.append("await page.keyboard.press('Enter');")
        if args.wait_after_ms > 0:
            actions.append(f"await page.waitForTimeout({int(args.wait_after_ms)});")

        page = await _run_firecrawl_code(
            session,
            api_key,
            base_url,
            _firecrawl_action_code(actions, args.capture_screenshot),
        )
        if page is None:
            return ToolResult.failure("Firecrawl browser_type failed.")
        resolved_url = str(page.get("url") or session.current_url)
        session.current_url = resolved_url
        artifact_ref = (
            _write_screenshot(context, f"type-{resolved_url}", page.get("screenshot"))
            if args.capture_screenshot
            else None
        )
        return ToolResult.success(
            content={
                "url": resolved_url,
                "title": str(page.get("title") or resolved_url),
                "status": 200,
                "action": "type",
                "target": args.selector or "focused",
                "elements": page.get("elements") or [],
                "text_excerpt": page.get("text_excerpt") or "",
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    if isinstance(session, MCPBrowserSession):
        type_tool = session.tool_map.get("type")
        if not type_tool:
            return ToolResult.failure("MCP browser server does not expose browser_type.")
        payload: dict[str, Any] = {"text": args.text, "submit": args.press_enter}
        if args.selector:
            payload["element"] = args.selector
            payload["ref"] = args.selector.lstrip("@")
        output, is_err = await _call_mcp_browser(session.server_name, type_tool, payload, context)
        if is_err:
            return ToolResult.failure(output)
        return ToolResult.success(
            content={
                "url": session.current_url,
                "action": "type",
                "target": args.selector or "focused",
                "output": output[:2000],
                **_session_fields(session),
            }
        )

    return ToolResult.failure("Unsupported browser session state.")


async def browser_scroll(args: BrowserScrollArgs, context: ToolContext) -> ToolResult:
    """Scroll the active viewport or target element and capture updated state."""
    session = _active_session(context)
    if session is None:
        return ToolResult.failure(
            "No active browser session. Call browser_navigate(url=...) first."
        )
    resolved_sel = _resolve_selector(args.selector)
    dx = args.amount if args.direction == "right" else (-args.amount if args.direction == "left" else 0)
    dy = args.amount if args.direction == "down" else (-args.amount if args.direction == "up" else 0)

    if isinstance(session, LocalCDPSession):
        if resolved_sel:
            sel_json = json.dumps(resolved_sel)
            if args.direction == "top":
                await session.eval_js(f"document.querySelector({sel_json})?.scrollTo(0, 0)")
            elif args.direction == "bottom":
                await session.eval_js(
                    f"(() => {{ const e = document.querySelector({sel_json}); if (e) e.scrollTop = e.scrollHeight; }})()"
                )
            else:
                await session.eval_js(f"document.querySelector({sel_json})?.scrollBy({dx}, {dy})")
        else:
            if args.direction == "top":
                await session.eval_js("window.scrollTo(0, 0)")
            elif args.direction == "bottom":
                await session.eval_js("window.scrollTo(0, document.body.scrollHeight)")
            else:
                await session.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": session.viewport_width / 2,
                        "y": session.viewport_height / 2,
                        "deltaX": dx,
                        "deltaY": dy,
                    },
                )
        await asyncio.sleep(0.25)
        return await _cdp_snapshot_payload(
            session,
            context,
            tag=f"scroll-{args.direction}",
            capture_screenshot=args.capture_screenshot,
            extra={"action": "scroll", "direction": args.direction, "amount": args.amount},
        )

    if isinstance(session, LiveSession):
        api_key, base_url = _firecrawl_key(context)
        if not api_key:
            return ToolResult.failure("FIRECRAWL_API_KEY is required for live browser scroll.")
        if resolved_sel:
            sel_json = json.dumps(resolved_sel)
            if args.direction == "top":
                js = f"await page.evaluate((s) => document.querySelector(s)?.scrollTo(0, 0), {sel_json});"
            elif args.direction == "bottom":
                js = f"await page.evaluate((s) => {{ const e = document.querySelector(s); if (e) e.scrollTop = e.scrollHeight; }}, {sel_json});"
            else:
                js = f"await page.evaluate(([s, x, y]) => document.querySelector(s)?.scrollBy(x, y), [{sel_json}, {dx}, {dy}]);"
        else:
            if args.direction == "top":
                js = "await page.evaluate(() => window.scrollTo(0, 0));"
            elif args.direction == "bottom":
                js = "await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));"
            else:
                js = f"await page.mouse.wheel({dx}, {dy});"

        page = await _run_firecrawl_code(
            session,
            api_key,
            base_url,
            _firecrawl_action_code([js, "await page.waitForTimeout(250);"], args.capture_screenshot),
        )
        if page is None:
            return ToolResult.failure("Firecrawl browser_scroll failed.")
        resolved_url = str(page.get("url") or session.current_url)
        artifact_ref = (
            _write_screenshot(context, f"scroll-{args.direction}", page.get("screenshot"))
            if args.capture_screenshot
            else None
        )
        return ToolResult.success(
            content={
                "url": resolved_url,
                "title": str(page.get("title") or resolved_url),
                "status": 200,
                "action": "scroll",
                "direction": args.direction,
                "elements": page.get("elements") or [],
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    if isinstance(session, MCPBrowserSession):
        key_name = "PageDown" if args.direction in ("down", "bottom") else "PageUp"
        press_tool = session.tool_map.get("press_key")
        if not press_tool:
            return ToolResult.failure("MCP browser server does not support scrolling.")
        output, is_err = await _call_mcp_browser(
            session.server_name, press_tool, {"key": key_name}, context
        )
        if is_err:
            return ToolResult.failure(output)
        return ToolResult.success(
            content={
                "url": session.current_url,
                "action": "scroll",
                "direction": args.direction,
                "output": output[:2000],
                **_session_fields(session),
            }
        )

    return ToolResult.failure("Unsupported browser session state.")


async def browser_press_key(args: BrowserPressKeyArgs, context: ToolContext) -> ToolResult:
    """Dispatch a keyboard key or shortcut to the active browser session."""
    session = _active_session(context)
    if session is None:
        return ToolResult.failure(
            "No active browser session. Call browser_navigate(url=...) first."
        )

    if isinstance(session, LocalCDPSession):
        base_key, vk, modifiers = _cdp_key_spec(args.key)
        mod_mask = _cdp_modifier_mask(modifiers)
        for ev_type in ("rawKeyDown", "keyUp"):
            await session.send(
                "Input.dispatchKeyEvent",
                {
                    "type": ev_type,
                    "key": base_key,
                    "code": base_key if len(base_key) > 1 else f"Key{base_key.upper()}",
                    "windowsVirtualKeyCode": vk,
                    "modifiers": mod_mask,
                },
            )
        if args.wait_after_ms > 0:
            await asyncio.sleep(args.wait_after_ms / 1000.0)
        await _cdp_wait_ready(session)
        return await _cdp_snapshot_payload(
            session,
            context,
            tag=f"key-{args.key}",
            capture_screenshot=args.capture_screenshot,
            extra={"action": "press_key", "key": args.key},
        )

    if isinstance(session, LiveSession):
        api_key, base_url = _firecrawl_key(context)
        if not api_key:
            return ToolResult.failure("FIRECRAWL_API_KEY is required for live browser press_key.")
        actions = [f"await page.keyboard.press({json.dumps(args.key)});"]
        if args.wait_after_ms > 0:
            actions.append(f"await page.waitForTimeout({int(args.wait_after_ms)});")
        page = await _run_firecrawl_code(
            session,
            api_key,
            base_url,
            _firecrawl_action_code(actions, args.capture_screenshot),
        )
        if page is None:
            return ToolResult.failure(f"Firecrawl browser_press_key failed for {args.key!r}.")
        resolved_url = str(page.get("url") or session.current_url)
        session.current_url = resolved_url
        artifact_ref = (
            _write_screenshot(context, f"key-{args.key}", page.get("screenshot"))
            if args.capture_screenshot
            else None
        )
        return ToolResult.success(
            content={
                "url": resolved_url,
                "title": str(page.get("title") or resolved_url),
                "status": 200,
                "action": "press_key",
                "key": args.key,
                "elements": page.get("elements") or [],
                **_session_fields(session),
            },
            artifact_ref=artifact_ref,
        )

    if isinstance(session, MCPBrowserSession):
        press_tool = session.tool_map.get("press_key")
        if not press_tool:
            return ToolResult.failure("MCP browser server does not expose browser_press_key.")
        output, is_err = await _call_mcp_browser(
            session.server_name, press_tool, {"key": args.key}, context
        )
        if is_err:
            return ToolResult.failure(output)
        return ToolResult.success(
            content={
                "url": session.current_url,
                "action": "press_key",
                "key": args.key,
                "output": output[:2000],
                **_session_fields(session),
            }
        )

    return ToolResult.failure("Unsupported browser session state.")


async def browser_extract(args: BrowserExtractArgs, context: ToolContext) -> ToolResult:
    """Extract clean structured content from the active browser DOM or a webpage URL."""
    session = _active_session(context)
    resolved_sel = _resolve_selector(args.selector)

    # Active live session extraction when url is omitted or matches the open page
    if session is not None and (args.url is None or _normalize_url(args.url) == session.current_url):
        if isinstance(session, LocalCDPSession):
            data = await session.eval_js(
                f"({_EXTRACT_DOM_JS})({json.dumps(resolved_sel)}, {json.dumps(args.only_main_content)}, {json.dumps(args.format)})"
            )
            if isinstance(data, dict):
                return ToolResult.success(
                    content={
                        "url": data.get("url") or session.current_url,
                        "markdown": data.get("content") or "",
                        "title": data.get("title") or "Page",
                        "status": 200,
                        **_session_fields(session),
                    }
                )
        elif isinstance(session, LiveSession):
            api_key, base_url = _firecrawl_key(context)
            if api_key:
                code = (
                    f"const out = await page.evaluate({_EXTRACT_DOM_JS}, "
                    f"{json.dumps(resolved_sel)}, {json.dumps(args.only_main_content)}, {json.dumps(args.format)});\n"
                    "JSON.stringify(out);"
                )
                data = await _run_firecrawl_code(session, api_key, base_url, code)
                if isinstance(data, dict):
                    return ToolResult.success(
                        content={
                            "url": data.get("url") or session.current_url,
                            "markdown": data.get("content") or "",
                            "title": data.get("title") or "Page",
                            "status": 200,
                            **_session_fields(session),
                        }
                    )

    if args.url is None:
        return ToolResult.failure(
            "No active browser session. Provide 'url' or call browser_navigate first."
        )

    api_key, base_url = _firecrawl_key(context)
    url_clean = _normalize_url(args.url)
    driver, _, _ = _resolve_driver(context)
    if driver == "disabled":
        return ToolResult.failure(
            "Browser automation is disabled in Settings → Connectors."
        )
    if driver.startswith("blocked:"):
        blocked_name = driver.split(":", 1)[1]
        return ToolResult.failure(
            f"Browser driver {blocked_name!r} is disabled in Settings → Connectors."
        )
    reason = "No FIRECRAWL_API_KEY configured in the vault or environment."

    if driver == "firecrawl" and api_key:
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
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl extract attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    continue
            except Exception as exc:  # noqa: BLE001
                reason = f"Firecrawl request failed ({type(exc).__name__}): {exc}"
                logger.warning("Firecrawl extract failed", exc_info=True)
                break

    elif driver == "cdp":
        try:
            cdp_session = await _ensure_cdp_session(context, get_settings())
            if cdp_session.current_url != url_clean:
                await cdp_session.send("Page.navigate", {"url": url_clean})
                await _cdp_wait_ready(cdp_session)
                cdp_session.current_url = url_clean
            data = await cdp_session.eval_js(
                f"({_EXTRACT_DOM_JS})({json.dumps(resolved_sel)}, {json.dumps(args.only_main_content)}, {json.dumps(args.format)})"
            )
            if isinstance(data, dict):
                return ToolResult.success(
                    content={
                        "url": data.get("url") or url_clean,
                        "markdown": data.get("content") or "",
                        "title": data.get("title") or "Page",
                        "status": 200,
                        **_session_fields(cdp_session),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Local Chromium CDP extract failed: {exc}")

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


def _parse_cua_json(raw_text: str) -> dict[str, Any] | None:
    """Extract a JSON object from a vision model's CUA response."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else None
            except ValueError:
                return None
        return None


async def browser_cua_task(args: BrowserCUATaskArgs, context: ToolContext) -> ToolResult:
    """Run an autonomous vision-guided CUA action loop toward a UI goal."""
    describe = getattr(context.llm, "describe_image", None)
    if not callable(describe):
        return ToolResult.failure(
            "browser_cua_task requires a vision-capable model client (describe_image). "
            "Use browser_navigate, browser_snapshot, browser_click, and browser_type directly."
        )

    last_artifact: str | None = None
    last_content: dict[str, Any] = {}
    actions_taken: list[dict[str, Any]] = []

    if args.start_url:
        nav_res = await browser_navigate(
            BrowserNavigateArgs(url=args.start_url, capture_screenshot=True), context
        )
        if not nav_res.ok:
            return nav_res
        last_artifact = nav_res.artifact_ref
        last_content = dict(nav_res.content or {})
    elif _active_session(context) is None:
        return ToolResult.failure(
            "No active browser session. Provide start_url or call browser_navigate first."
        )
    else:
        snap_res = await browser_snapshot(BrowserSnapshotArgs(capture_screenshot=True), context)
        if not snap_res.ok:
            return snap_res
        last_artifact = snap_res.artifact_ref
        last_content = dict(snap_res.content or {})

    for step_idx in range(args.max_steps):
        if not last_artifact or not Path(last_artifact).is_file():
            return ToolResult.failure("CUA loop lost its viewport screenshot artifact.")
        png_bytes = Path(last_artifact).read_bytes()
        elements_summary = json.dumps((last_content.get("elements") or [])[:40], indent=2)
        history_summary = json.dumps(actions_taken[-5:])
        prompt = (
            f"You are a Computer Use Agent driving a web browser.\n"
            f"Goal: {args.goal}\n"
            f"Current URL: {last_content.get('url', '')}\n"
            f"Step {step_idx + 1} of {args.max_steps}.\n"
            f"Recent actions: {history_summary}\n"
            f"Interactive elements on screen:\n{elements_summary}\n\n"
            "Respond with ONLY one JSON object specifying the next action:\n"
            '  {"action": "click", "selector": "@e1"} or {"action": "click", "x": 100, "y": 200}\n'
            '  {"action": "type", "selector": "@e2", "text": "...", "press_enter": true}\n'
            '  {"action": "scroll", "direction": "down", "amount": 500}\n'
            '  {"action": "press_key", "key": "Enter"}\n'
            '  {"action": "done", "summary": "what was achieved"}\n'
            '  {"action": "fail", "summary": "why goal cannot be completed"}'
        )
        raw_reply = await describe(png_bytes, "image/png", question=prompt)
        decision = _parse_cua_json(str(raw_reply))
        if not decision or not isinstance(decision.get("action"), str):
            return ToolResult.failure(
                f"Vision model returned invalid CUA decision at step {step_idx + 1}: {str(raw_reply)[:200]}"
            )
        action = decision["action"].strip().lower()
        actions_taken.append({"step": step_idx + 1, **decision})

        if action == "done":
            return ToolResult.success(
                content={
                    **last_content,
                    "action": "cua_task",
                    "goal": args.goal,
                    "completed": True,
                    "summary": str(decision.get("summary") or "Goal completed."),
                    "actions_taken": actions_taken,
                },
                artifact_ref=last_artifact,
            )
        if action == "fail":
            return ToolResult.failure(
                str(decision.get("summary") or "CUA task aborted."),
                content={**last_content, "actions_taken": actions_taken},
            )

        if action == "click":
            step_res = await browser_click(
                BrowserClickArgs(
                    selector=decision.get("selector"),
                    x=decision.get("x"),
                    y=decision.get("y"),
                    capture_screenshot=True,
                ),
                context,
            )
        elif action == "type":
            step_res = await browser_type(
                BrowserTypeArgs(
                    text=str(decision.get("text") or ""),
                    selector=decision.get("selector"),
                    x=decision.get("x"),
                    y=decision.get("y"),
                    press_enter=bool(decision.get("press_enter", False)),
                    capture_screenshot=True,
                ),
                context,
            )
        elif action == "scroll":
            step_res = await browser_scroll(
                BrowserScrollArgs(
                    direction=str(decision.get("direction") or "down"),  # type: ignore[arg-type]
                    amount=int(decision.get("amount") or 500),
                    capture_screenshot=True,
                ),
                context,
            )
        elif action == "press_key":
            step_res = await browser_press_key(
                BrowserPressKeyArgs(
                    key=str(decision.get("key") or "Enter"),
                    capture_screenshot=True,
                ),
                context,
            )
        elif action == "navigate" and decision.get("url"):
            step_res = await browser_navigate(
                BrowserNavigateArgs(url=str(decision["url"]), capture_screenshot=True),
                context,
            )
        else:
            return ToolResult.failure(f"Unknown CUA action {action!r} at step {step_idx + 1}.")

        if not step_res.ok:
            return ToolResult.failure(
                f"CUA step {step_idx + 1} ({action}) failed: {step_res.error}",
                content={**last_content, "actions_taken": actions_taken},
            )
        last_artifact = step_res.artifact_ref or last_artifact
        last_content = dict(step_res.content or {})

    return ToolResult.success(
        content={
            **last_content,
            "action": "cua_task",
            "goal": args.goal,
            "completed": False,
            "summary": f"Reached max_steps ({args.max_steps}).",
            "actions_taken": actions_taken,
        },
        artifact_ref=last_artifact,
    )


# =============================================================================
# Tool Definitions
# =============================================================================


browser_navigate_tool = Tool.define(
    name="browser_navigate",
    description=(
        "Navigate the interactive CUA browser to a URL, index clickable elements (@e1, @e2, ... with x/y coordinates), "
        "capture a PNG screenshot, and stream the browser window to the chat."
    ),
    level="local",
    args_model=BrowserNavigateArgs,
    handler=browser_navigate,
)

browser_snapshot_tool = Tool.define(
    name="browser_snapshot",
    description=(
        "Inspect the current active browser page without navigating: capture a fresh screenshot, "
        "visible text excerpt, and indexed interactive elements (@eN refs and viewport x/y coordinates)."
    ),
    level="local",
    args_model=BrowserSnapshotArgs,
    handler=browser_snapshot,
)

browser_click_tool = Tool.define(
    name="browser_click",
    description=(
        "Click an element in the active browser session by '@eN' ref, CSS selector, or viewport (x, y) coordinates. "
        "Returns updated interactive elements and a fresh screenshot."
    ),
    level="local",
    args_model=BrowserClickArgs,
    handler=browser_click,
)

browser_type_tool = Tool.define(
    name="browser_type",
    description=(
        "Type text into an input in the active browser session (targeted by '@eN' ref, CSS selector, or x/y coordinates), "
        "optionally clearing existing text and pressing Enter."
    ),
    level="local",
    args_model=BrowserTypeArgs,
    handler=browser_type,
)

browser_scroll_tool = Tool.define(
    name="browser_scroll",
    description="Scroll the active browser viewport or a scrollable element (up, down, left, right, top, bottom) and capture a screenshot.",
    level="local",
    args_model=BrowserScrollArgs,
    handler=browser_scroll,
)

browser_press_key_tool = Tool.define(
    name="browser_press_key",
    description="Press a keyboard key or shortcut (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown', 'Control+a') in the active browser session.",
    level="local",
    args_model=BrowserPressKeyArgs,
    handler=browser_press_key,
)

browser_extract_tool = Tool.define(
    name="browser_extract",
    description=(
        "Extract clean structured markdown or HTML from the currently open live browser DOM (when url is omitted) "
        "or from a specified webpage URL."
    ),
    level="local",
    args_model=BrowserExtractArgs,
    handler=browser_extract,
)

browser_cua_task_tool = Tool.define(
    name="browser_cua_task",
    description=(
        "Execute an autonomous vision-guided multi-step browser interaction toward a UI goal "
        "(navigating, clicking, typing, scrolling) and return the final state and screenshot."
    ),
    level="local",
    args_model=BrowserCUATaskArgs,
    handler=browser_cua_task,
)

BROWSER_TOOLS = (
    browser_navigate_tool,
    browser_snapshot_tool,
    browser_click_tool,
    browser_type_tool,
    browser_scroll_tool,
    browser_press_key_tool,
    browser_extract_tool,
    browser_cua_task_tool,
)

__all__ = [
    "BROWSER_TOOLS",
    "FALLBACK_PNG_BYTES",
    "SESSION_ACTIVITY_TTL_SECONDS",
    "SESSION_TTL_SECONDS",
    "BrowserCUATaskArgs",
    "BrowserClickArgs",
    "BrowserExtractArgs",
    "BrowserNavigateArgs",
    "BrowserPressKeyArgs",
    "BrowserScrollArgs",
    "BrowserSnapshotArgs",
    "BrowserTypeArgs",
    "LiveSession",
    "LocalCDPSession",
    "MCPBrowserSession",
    "browser_click",
    "browser_click_tool",
    "browser_cua_task",
    "browser_cua_task_tool",
    "browser_extract",
    "browser_extract_tool",
    "browser_navigate",
    "browser_navigate_tool",
    "browser_press_key",
    "browser_press_key_tool",
    "browser_scroll",
    "browser_scroll_tool",
    "browser_snapshot",
    "browser_snapshot_tool",
    "browser_type",
    "browser_type_tool",
    "close_browser_session",
]
