"""Standalone Gmail Model Context Protocol (MCP) server providing search, read, draft, and send tools."""
from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any
import httpx

from connectors.gmail.auth import GmailAuthManager, OAuthCredentials
from connectors.gmail.sync import GMAIL_API_BASE, GmailSyncEngine


class GmailMCPServer:
    """Gmail MCP server implementing standard tool definitions and execution."""

    TOOL_DEFINITIONS = [
        {
            "name": "gmail_search",
            "description": "Search email messages using Gmail search syntax (e.g. 'is:unread', 'from:alice', 'newer_than:2d').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query."},
                    "max_results": {"type": "integer", "default": 10, "description": "Maximum number of messages to return."},
                },
                "required": ["query"],
            },
            "approval_level": "read",
        },
        {
            "name": "gmail_read",
            "description": "Fetch full email body, sender, recipient headers, and thread ID for a specific message.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Unique Gmail message ID."},
                },
                "required": ["message_id"],
            },
            "approval_level": "read",
        },
        {
            "name": "gmail_create_draft",
            "description": "Create a new email draft in Gmail without sending it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Plain text email body."},
                    "thread_id": {"type": "string", "description": "Optional thread ID to reply within an existing conversation."},
                },
                "required": ["to", "subject", "body"],
            },
            "approval_level": "read",
        },
        {
            "name": "gmail_send",
            "description": "Send an email immediately via Gmail (requires external human approval policy).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body content."},
                    "thread_id": {"type": "string", "description": "Optional thread ID for conversational replies."},
                },
                "required": ["to", "subject", "body"],
            },
            "approval_level": "external",
        },
    ]

    def __init__(self, auth_manager: GmailAuthManager, credentials: OAuthCredentials | None = None) -> None:
        self.auth_manager = auth_manager
        self.credentials = credentials
        self.sync_engine = GmailSyncEngine(auth_manager)

    def list_tools(self) -> list[dict[str, Any]]:
        """Return available tool definitions."""
        return self.TOOL_DEFINITIONS

    def get_tool_approval_level(self, tool_name: str) -> str:
        """Return 'read' or 'external' approval requirement."""
        for t in self.TOOL_DEFINITIONS:
            if t["name"] == tool_name:
                return t["approval_level"]
        return "external"

    async def _get_auth_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        if not self.credentials:
            raise RuntimeError("Gmail MCP Server credentials not configured")
        token = await self.sync_engine._get_valid_token(self.credentials, client)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Dispatch tool execution to corresponding Gmail API call."""
        http_client = client or httpx.AsyncClient(timeout=15.0)
        try:
            if tool_name == "gmail_search":
                return await self._search(arguments, http_client)
            elif tool_name == "gmail_read":
                return await self._read(arguments, http_client)
            elif tool_name == "gmail_create_draft":
                return await self._create_draft(arguments, http_client)
            elif tool_name == "gmail_send":
                return await self._send(arguments, http_client)
            else:
                return {"is_error": True, "error": f"Unknown tool '{tool_name}'"}
        finally:
            if client is None:
                await http_client.aclose()

    async def _search(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        query = args.get("query", "")
        max_results = min(args.get("max_results", 10), 50)
        headers = await self._get_auth_headers(client)

        res = await client.get(
            f"{GMAIL_API_BASE}/messages",
            headers=headers,
            params={"q": query, "maxResults": max_results},
        )
        res.raise_for_status()
        messages = res.json().get("messages", [])

        results = []
        for m in messages:
            try:
                detail = await self.sync_engine.fetch_message_detail(m["id"], self.credentials, client)
                results.append({
                    "id": detail["id"],
                    "thread_id": detail["thread_id"],
                    "subject": detail["subject"],
                    "sender": detail["sender"],
                    "snippet": detail["snippet"],
                })
            except Exception:
                results.append({"id": m["id"], "thread_id": m.get("threadId")})
        return {"messages": results, "total": len(results)}

    async def _read(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        message_id = args.get("message_id", "")
        if not message_id:
            return {"is_error": True, "error": "Missing message_id"}
        detail = await self.sync_engine.fetch_message_detail(message_id, self.credentials, client)
        return detail

    async def _create_draft(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        headers = await self._get_auth_headers(client)
        msg = MIMEText(args["body"])
        msg["to"] = ", ".join(args["to"]) if isinstance(args["to"], list) else args["to"]
        msg["subject"] = args["subject"]

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        draft_body: dict[str, Any] = {"message": {"raw": raw}}
        if args.get("thread_id"):
            draft_body["message"]["threadId"] = args["thread_id"]

        res = await client.post(
            f"{GMAIL_API_BASE}/drafts",
            headers=headers,
            json=draft_body,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "draft_id": data.get("id"),
            "message_id": data.get("message", {}).get("id"),
            "status": "draft_created",
        }

    async def _send(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        headers = await self._get_auth_headers(client)
        msg = MIMEText(args["body"])
        msg["to"] = ", ".join(args["to"]) if isinstance(args["to"], list) else args["to"]
        msg["subject"] = args["subject"]

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        send_body: dict[str, Any] = {"raw": raw}
        if args.get("thread_id"):
            send_body["threadId"] = args["thread_id"]

        res = await client.post(
            f"{GMAIL_API_BASE}/messages/send",
            headers=headers,
            json=send_body,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "message_id": data.get("id"),
            "thread_id": data.get("threadId"),
            "status": "sent",
        }
