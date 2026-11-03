"""Web search and fetch tools connected to the Exa MCP server.

Exa provides neural semantic web search, webpage content extraction, and crawling
over MCP. The free endpoint operates at ``https://mcp.exa.ai/mcp`` without requiring
an API key, and accepts an optional ``EXA_API_KEY`` for higher rate limits.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
import urllib.error
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config.settings import get_settings
from src.modules.runs.tools import Tool, ToolContext, ToolResult

EXA_USER_AGENT = "VigilAgent-ExaSearch/1.0 (https://exa.ai)"


class WebSearchExaArgs(BaseModel):
    """Arguments for the ``web_search_exa`` tool."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        ...,
        min_length=1,
        description=(
            "Natural language search query. A semantically rich description of the ideal page, "
            "not just raw keywords. Optionally include category:<type> (e.g. category:company or category:people)."
        ),
    )
    objective: str | None = Field(
        default=None,
        description=(
            "Goal for this search turn; describe which documents should rank first, "
            "which should be excluded, and what specific facts or figures to extract."
        ),
    )
    numResults: int | None = Field(
        default=8,
        description="Number of search results to return (default: 8, max: 20).",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_args(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize query alias
            if "query" not in data:
                for alias in ("q", "search_query", "searchTerm", "search"):
                    if alias in data:
                        data["query"] = data[alias]
                        break
            # Normalize numResults alias (snake_case, max_results, limit)
            if "numResults" not in data:
                for alias in ("num_results", "max_results", "maxResults", "limit", "count"):
                    if alias in data:
                        data["numResults"] = data[alias]
                        break
        return data


class WebFetchExaArgs(BaseModel):
    """Arguments for the ``web_fetch_exa`` tool."""

    model_config = ConfigDict(extra="ignore")

    urls: list[str] = Field(
        ...,
        min_length=1,
        description="URLs of webpages to read and extract as clean markdown.",
    )
    maxCharacters: int | None = Field(
        default=3000,
        description="Maximum characters to extract per page (default: 3000).",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_args(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Accept url singular or urls string/list
            if "urls" not in data and "url" in data:
                raw_url = data["url"]
                data["urls"] = [raw_url] if isinstance(raw_url, str) else list(raw_url)
            elif "urls" in data and isinstance(data["urls"], str):
                data["urls"] = [data["urls"]]
            # Normalize maxCharacters alias
            if "maxCharacters" not in data:
                for alias in ("max_characters", "maxChars", "max_chars", "limit"):
                    if alias in data:
                        data["maxCharacters"] = data[alias]
                        break
        return data


def _call_exa_mcp_sync(
    tool_name: str,
    arguments: dict[str, Any],
    exa_url: str,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> tuple[str, bool]:
    """Execute one tool call against the Exa MCP server via HTTP StreamMCP."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": EXA_USER_AGENT,
    }
    if api_key:
        headers["x-api-key"] = api_key

    req = urllib.request.Request(
        exa_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_msg = ""
        try:
            err_msg = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"Exa MCP HTTP {exc.code} error: {err_msg or exc.reason}", True
    except Exception as exc:
        return f"Exa MCP connection error: {exc}", True

    # Parse JSON or SSE data line
    data: dict[str, Any] | None = None
    for line in body.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("data:"):
            try:
                data = json.loads(trimmed[len("data:"):].strip())
                break
            except ValueError:
                continue
    if data is None:
        try:
            data = json.loads(body)
        except ValueError:
            return body[:1000], False

    if "error" in data:
        return f"Exa MCP returned error: {data['error']}", True

    result = data.get("result", {})
    is_error = bool(result.get("isError", False))
    content = result.get("content", [])

    # If an invalid API key error occurred, retry once anonymously via the free tier
    if is_error and api_key and any("Invalid API key" in str(item) for item in content):
        return _call_exa_mcp_sync(tool_name, arguments, exa_url, api_key=None, timeout=timeout)
    if isinstance(content, list):
        parts = [
            item.get("text", str(item)) if isinstance(item, dict) else str(item)
            for item in content
        ]
        return "\n\n".join(parts), is_error
    if isinstance(content, str):
        return content, is_error
    return json.dumps(result, indent=2), is_error


async def web_search_exa(args: WebSearchExaArgs, context: ToolContext) -> ToolResult:
    """Execute live neural web search via the free Exa MCP server."""
    settings = get_settings()
    arguments: dict[str, Any] = {"query": args.query}
    if args.objective:
        arguments["objective"] = args.objective
    else:
        arguments["objective"] = f"Find relevant, up-to-date information for: {args.query}"
    if args.numResults:
        arguments["numResults"] = min(max(args.numResults, 1), 20)

    output, is_error = await asyncio.to_thread(
        _call_exa_mcp_sync,
        "web_search_exa",
        arguments,
        settings.EXA_MCP_URL,
        settings.EXA_API_KEY or None,
    )
    if is_error:
        return ToolResult.failure(output)
    return ToolResult.success(output)


async def web_fetch_exa(args: WebFetchExaArgs, context: ToolContext) -> ToolResult:
    """Extract full markdown webpage content for URLs via the Exa MCP server."""
    settings = get_settings()
    arguments: dict[str, Any] = {"urls": args.urls}
    if args.maxCharacters:
        arguments["maxCharacters"] = args.maxCharacters

    output, is_error = await asyncio.to_thread(
        _call_exa_mcp_sync,
        "web_fetch_exa",
        arguments,
        settings.EXA_MCP_URL,
        settings.EXA_API_KEY or None,
    )
    if is_error:
        return ToolResult.failure(output)
    return ToolResult.success(output)


EXA_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="web_search",
        description=(
            "Search the live web for any topic using the Exa MCP search engine. "
            "Returns clean, parsed markdown highlights from top search results. "
            "Best for finding current facts, real-world documentation, news, companies, and people."
        ),
        level="local",
        args_model=WebSearchExaArgs,
        handler=web_search_exa,
    ),
    Tool.define(
        name="web_fetch",
        description=(
            "Extract full webpage content as clean markdown from one or more known URLs using Exa. "
            "Use to inspect full documentation pages or articles found via web_search."
        ),
        level="local",
        args_model=WebFetchExaArgs,
        handler=web_fetch_exa,
    ),
)


web_search = web_search_exa
web_fetch = web_fetch_exa

__all__ = [
    "EXA_TOOLS",
    "WebFetchExaArgs",
    "WebSearchExaArgs",
    "web_fetch",
    "web_fetch_exa",
    "web_search",
    "web_search_exa",
]
