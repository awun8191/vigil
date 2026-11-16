"""Standalone Gmail Model Context Protocol (MCP) server providing search, read, thread, draft, and send tools."""
from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import re
from typing import Any
import httpx

from connectors.gmail.auth import GmailAuthManager, OAuthCredentials
from connectors.gmail.sync import GMAIL_API_BASE, GmailSyncEngine, _html_to_readable_text


def markdown_to_email_html(text: str) -> str:
    """Convert Markdown or plain text into clean, email-client-safe inline HTML."""
    trimmed = text.strip()
    if not trimmed:
        return ""
    if re.search(r"<(p|div|table|ul|ol|li|h[1-6]|br|strong|em|a)\b", trimmed, re.IGNORECASE):
        return trimmed

    escaped = html.escape(trimmed)
    # Code blocks
    escaped = re.sub(
        r"```(?:\w+)?\n(.*?)```",
        r'<pre style="background:#f4f4f5;padding:12px;border-radius:8px;font-family:monospace;font-size:13px;overflow-x:auto;"><code>\1</code></pre>',
        escaped,
        flags=re.DOTALL,
    )
    # Inline code
    escaped = re.sub(
        r"`([^`\n]+)`",
        r'<code style="background:#f4f4f5;padding:2px 5px;border-radius:4px;font-family:monospace;font-size:0.9em;">\1</code>',
        escaped,
    )
    # Bold & Italic
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    # Links
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        r'<a href="\2" style="color:#111827;text-decoration:underline;">\1</a>',
        escaped,
    )

    blocks = re.split(r"\n{2,}", escaped)
    rendered_blocks: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith(("- ", "* ", "• ")) for line in lines):
            items = "".join(
                f'<li style="margin-bottom:4px;">{re.sub(r"^[-*•]\s+", "", line)}</li>'
                for line in lines
            )
            rendered_blocks.append(
                f'<ul style="margin:0 0 12px 0;padding-left:20px;">{items}</ul>'
            )
        elif all(re.match(r"^\d+\.\s+", line) for line in lines):
            items = "".join(
                f'<li style="margin-bottom:4px;">{re.sub(r"^\d+\.\s+", "", line)}</li>'
                for line in lines
            )
            rendered_blocks.append(
                f'<ol style="margin:0 0 12px 0;padding-left:20px;">{items}</ol>'
            )
        elif lines[0].startswith("### "):
            heading = lines[0][4:]
            rendered_blocks.append(
                f'<h3 style="margin:0 0 8px 0;font-size:16px;font-weight:600;">{heading}</h3>'
            )
        elif lines[0].startswith("## "):
            heading = lines[0][3:]
            rendered_blocks.append(
                f'<h2 style="margin:0 0 10px 0;font-size:18px;font-weight:600;">{heading}</h2>'
            )
        elif lines[0].startswith("# "):
            heading = lines[0][2:]
            rendered_blocks.append(
                f'<h1 style="margin:0 0 12px 0;font-size:20px;font-weight:600;">{heading}</h1>'
            )
        elif all(line.startswith("&gt;") for line in lines):
            quote_body = "<br>".join(
                re.sub(r"^&gt;\s?", "", line) for line in lines
            )
            rendered_blocks.append(
                f'<blockquote style="margin:0 0 12px 0;padding:8px 14px;border-left:3px solid #d4d4d8;color:#52525b;">{quote_body}</blockquote>'
            )
        else:
            paragraph = "<br>".join(lines)
            rendered_blocks.append(
                f'<p style="margin:0 0 12px 0;line-height:1.55;">{paragraph}</p>'
            )

    inner = "\n".join(rendered_blocks)
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        'font-size:14px;line-height:1.55;color:#18181b;">'
        f"{inner}</div>"
    )


def _format_address_header(val: str | list[str] | None) -> str:
    if not val:
        return ""
    if isinstance(val, list):
        return ", ".join(str(x).strip() for x in val if str(x).strip())
    return str(val).strip()


def _build_mime_message(args: dict[str, Any]) -> tuple[bytes, str, str]:
    """Build a multipart/alternative MIME message with plain and HTML bodies."""
    raw_body = str(args.get("body") or "")
    explicit_html = args.get("body_html")
    if isinstance(explicit_html, str) and explicit_html.strip():
        html_body = explicit_html.strip()
        plain_body = raw_body.strip() or _html_to_readable_text(html_body)
    else:
        plain_body = raw_body
        html_body = markdown_to_email_html(raw_body)

    msg = MIMEMultipart("alternative")
    msg["To"] = _format_address_header(args.get("to"))
    cc_str = _format_address_header(args.get("cc"))
    if cc_str:
        msg["Cc"] = cc_str
    bcc_str = _format_address_header(args.get("bcc"))
    if bcc_str:
        msg["Bcc"] = bcc_str
    msg["Subject"] = str(args.get("subject") or "")

    in_reply_to = args.get("in_reply_to")
    if isinstance(in_reply_to, str) and in_reply_to.strip():
        msg["In-Reply-To"] = in_reply_to.strip()
    references = args.get("references")
    if isinstance(references, str) and references.strip():
        msg["References"] = references.strip()
    elif isinstance(in_reply_to, str) and in_reply_to.strip():
        msg["References"] = in_reply_to.strip()

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    return msg.as_bytes(), plain_body, html_body


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
            "description": "Fetch full email body (plain text and HTML), sender, recipient headers, and thread ID for a specific message.",
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
            "name": "gmail_read_thread",
            "description": "Fetch all messages in an email conversation thread chronologically.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "Gmail conversation thread ID."},
                },
                "required": ["thread_id"],
            },
            "approval_level": "read",
        },
        {
            "name": "gmail_create_draft",
            "description": "Create a rich-formatted email draft in Gmail (supports Markdown and HTML) without sending it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses."},
                    "cc": {"type": "array", "items": {"type": "string"}, "description": "Optional CC recipient email addresses."},
                    "bcc": {"type": "array", "items": {"type": "string"}, "description": "Optional BCC recipient email addresses."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body (supports rich Markdown formatting or plain text)."},
                    "body_html": {"type": "string", "description": "Optional explicit HTML body for rich email layout."},
                    "thread_id": {"type": "string", "description": "Optional thread ID to reply within an existing conversation."},
                    "in_reply_to": {"type": "string", "description": "Optional RFC 2822 Message-ID header of the message being replied to."},
                },
                "required": ["to", "subject", "body"],
            },
            "approval_level": "read",
        },
        {
            "name": "gmail_update_draft",
            "description": "Update an existing Gmail draft with revised recipients, subject, or rich-formatted body.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string", "description": "Existing Gmail draft ID to update."},
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses."},
                    "cc": {"type": "array", "items": {"type": "string"}, "description": "Optional CC recipient email addresses."},
                    "bcc": {"type": "array", "items": {"type": "string"}, "description": "Optional BCC recipient email addresses."},
                    "subject": {"type": "string", "description": "Updated email subject line."},
                    "body": {"type": "string", "description": "Updated email body (Markdown or plain text)."},
                    "body_html": {"type": "string", "description": "Optional updated explicit HTML body."},
                    "thread_id": {"type": "string", "description": "Optional thread ID."},
                },
                "required": ["draft_id", "to", "subject", "body"],
            },
            "approval_level": "local",
        },
        {
            "name": "gmail_send",
            "description": "Send a rich-formatted email via Gmail (presents an editable email card for human approval before sending).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses."},
                    "cc": {"type": "array", "items": {"type": "string"}, "description": "Optional CC recipient email addresses."},
                    "bcc": {"type": "array", "items": {"type": "string"}, "description": "Optional BCC recipient email addresses."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body content (supports Markdown or plain text)."},
                    "body_html": {"type": "string", "description": "Optional explicit HTML body for rich email layout."},
                    "thread_id": {"type": "string", "description": "Optional thread ID for conversational replies."},
                    "in_reply_to": {"type": "string", "description": "Optional RFC 2822 Message-ID header of the parent message."},
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
        """Return 'read', 'local', or 'external' approval requirement."""
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
            elif tool_name == "gmail_read_thread":
                return await self._read_thread(arguments, http_client)
            elif tool_name == "gmail_create_draft":
                return await self._create_draft(arguments, http_client)
            elif tool_name == "gmail_update_draft":
                return await self._update_draft(arguments, http_client)
            elif tool_name == "gmail_send":
                return await self._send(arguments, http_client)
            else:
                return {"is_error": True, "error": f"Unknown tool '{tool_name}'"}
        finally:
            if client is None:
                await http_client.aclose()

    async def _search(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        query = args.get("query", "")
        max_results = min(int(args.get("max_results") or 10), 25)
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
                detail = await self.sync_engine.fetch_message_detail(m["id"], self.credentials, client)  # type: ignore[arg-type]
                results.append({
                    "id": detail["id"],
                    "thread_id": detail["thread_id"],
                    "subject": detail["subject"],
                    "sender": detail["sender"],
                    "date": detail.get("date", ""),
                    "snippet": detail["snippet"],
                })
            except Exception:
                results.append({"id": m["id"], "thread_id": m.get("threadId")})
        return {"messages": results, "total": len(results)}

    async def _read(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        message_id = args.get("message_id", "")
        if not message_id:
            return {"is_error": True, "error": "Missing message_id"}
        return await self.sync_engine.fetch_message_detail(message_id, self.credentials, client)  # type: ignore[arg-type]

    async def _read_thread(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        thread_id = args.get("thread_id", "")
        if not thread_id:
            return {"is_error": True, "error": "Missing thread_id"}
        return await self.sync_engine.fetch_thread_detail(thread_id, self.credentials, client)  # type: ignore[arg-type]

    async def _populate_reply_headers(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        """Populate In-Reply-To and References from the thread's latest message when absent."""
        enriched = dict(args)
        thread_id = enriched.get("thread_id")
        if thread_id and not enriched.get("in_reply_to"):
            try:
                thread = await self.sync_engine.fetch_thread_detail(
                    str(thread_id), self.credentials, client  # type: ignore[arg-type]
                )
                msgs = thread.get("messages") or []
                if msgs:
                    last_msg = msgs[-1]
                    msg_id_hdr = last_msg.get("message_id_header")
                    refs = last_msg.get("references")
                    if msg_id_hdr:
                        enriched["in_reply_to"] = msg_id_hdr
                        enriched["references"] = f"{refs} {msg_id_hdr}".strip() if refs else msg_id_hdr
            except Exception:
                pass
        return enriched

    async def _create_draft(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        headers = await self._get_auth_headers(client)
        enriched = await self._populate_reply_headers(args, client)
        raw_bytes, plain_body, html_body = _build_mime_message(enriched)

        raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
        draft_body: dict[str, Any] = {"message": {"raw": raw}}
        if enriched.get("thread_id"):
            draft_body["message"]["threadId"] = enriched["thread_id"]

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
            "thread_id": enriched.get("thread_id") or data.get("message", {}).get("threadId"),
            "to": enriched.get("to"),
            "cc": enriched.get("cc"),
            "subject": enriched.get("subject"),
            "body": plain_body,
            "body_html": html_body,
            "status": "draft_created",
        }

    async def _update_draft(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        draft_id = str(args.get("draft_id") or "").strip()
        if not draft_id:
            return {"is_error": True, "error": "Missing draft_id"}
        headers = await self._get_auth_headers(client)
        enriched = await self._populate_reply_headers(args, client)
        raw_bytes, plain_body, html_body = _build_mime_message(enriched)

        raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
        draft_body: dict[str, Any] = {"id": draft_id, "message": {"raw": raw}}
        if enriched.get("thread_id"):
            draft_body["message"]["threadId"] = enriched["thread_id"]

        res = await client.put(
            f"{GMAIL_API_BASE}/drafts/{draft_id}",
            headers=headers,
            json=draft_body,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "draft_id": data.get("id", draft_id),
            "message_id": data.get("message", {}).get("id"),
            "thread_id": enriched.get("thread_id") or data.get("message", {}).get("threadId"),
            "to": enriched.get("to"),
            "cc": enriched.get("cc"),
            "subject": enriched.get("subject"),
            "body": plain_body,
            "body_html": html_body,
            "status": "draft_updated",
        }

    async def _send(self, args: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        headers = await self._get_auth_headers(client)
        enriched = await self._populate_reply_headers(args, client)
        raw_bytes, plain_body, html_body = _build_mime_message(enriched)

        raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
        send_body: dict[str, Any] = {"raw": raw}
        if enriched.get("thread_id"):
            send_body["threadId"] = enriched["thread_id"]

        res = await client.post(
            f"{GMAIL_API_BASE}/messages/send",
            headers=headers,
            json=send_body,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "message_id": data.get("id"),
            "thread_id": data.get("threadId") or enriched.get("thread_id"),
            "to": enriched.get("to"),
            "cc": enriched.get("cc"),
            "subject": enriched.get("subject"),
            "body": plain_body,
            "body_html": html_body,
            "status": "sent",
        }
