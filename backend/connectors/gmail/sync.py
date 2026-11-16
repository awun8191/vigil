"""Gmail synchronization engine supporting historyId delta sync, recursive MIME parsing, and 7-day fallback."""
from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
import html
import re
from typing import Any
import httpx

from connectors.gmail.auth import OAuthCredentials, GmailAuthManager


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _html_to_readable_text(raw_html: str) -> str:
    """Convert HTML email body into clean, readable plain text / markdown-lite."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(
        r'(?is)<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    unescaped = html.unescape(text)
    lines = [line.rstrip() for line in unescaped.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _extract_mime_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    """Recursively walk a Gmail MIME payload and return (plain_text, html_text)."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def _walk(node: dict[str, Any]) -> None:
        mime_type = str(node.get("mimeType") or "").lower()
        body_data = (node.get("body") or {}).get("data")
        if isinstance(body_data, str) and body_data:
            try:
                decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
                if mime_type == "text/plain":
                    plain_parts.append(decoded)
                elif mime_type == "text/html":
                    html_parts.append(decoded)
            except Exception:
                pass
        for child in node.get("parts") or []:
            if isinstance(child, dict):
                _walk(child)

    _walk(payload)
    plain_text = "\n\n".join(p.strip() for p in plain_parts if p.strip())
    html_text = "\n".join(h.strip() for h in html_parts if h.strip())
    if not plain_text and html_text:
        plain_text = _html_to_readable_text(html_text)
    return plain_text, html_text


class GmailSyncEngine:
    """Synchronizes messages using incremental historyId tracking or bounded 7-day fallback."""

    def __init__(self, auth_manager: GmailAuthManager) -> None:
        self.auth_manager = auth_manager

    async def _get_valid_token(
        self,
        creds: OAuthCredentials,
        client: httpx.AsyncClient,
    ) -> str:
        """Ensure token is valid, refreshing if expired."""
        if creds.is_expired:
            await self.auth_manager.refresh_access_token(creds, http_client=client)
        return creds.access_token

    async def get_profile_history_id(
        self,
        creds: OAuthCredentials,
        client: httpx.AsyncClient,
    ) -> str:
        """Fetch current historyId from user profile."""
        token = await self._get_valid_token(creds, client)
        res = await client.get(
            f"{GMAIL_API_BASE}/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        res.raise_for_status()
        return str(res.json().get("historyId", "1"))

    def _parse_message_resource(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw Gmail message resource into a normalized dictionary."""
        payload = raw.get("payload", {})
        headers = {
            str(h.get("name", "")).lower(): str(h.get("value", ""))
            for h in payload.get("headers", [])
            if isinstance(h, dict)
        }
        subject = headers.get("subject", "No Subject")
        sender = headers.get("from", "Unknown")
        to = headers.get("to", "")
        cc = headers.get("cc", "")
        date_str = headers.get("date", "")
        message_id_header = headers.get("message-id", "")
        in_reply_to = headers.get("in-reply-to", "")
        references = headers.get("references", "")

        plain_body, html_body = _extract_mime_bodies(payload)
        snippet = str(raw.get("snippet", ""))
        body = plain_body or snippet

        return {
            "id": raw["id"],
            "thread_id": raw.get("threadId", raw["id"]),
            "subject": subject,
            "sender": sender,
            "to": to,
            "cc": cc,
            "date": date_str,
            "message_id_header": message_id_header,
            "in_reply_to": in_reply_to,
            "references": references,
            "snippet": snippet,
            "body": body,
            "body_html": html_body or None,
            "history_id": raw.get("historyId"),
            "labels": raw.get("labelIds", []),
        }

    async def fetch_message_detail(
        self,
        message_id: str,
        creds: OAuthCredentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Fetch full message details including headers, plain body, and HTML body."""
        token = await self._get_valid_token(creds, client)
        res = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        )
        res.raise_for_status()
        return self._parse_message_resource(res.json())

    async def fetch_thread_detail(
        self,
        thread_id: str,
        creds: OAuthCredentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Fetch all messages in a conversation thread chronologically."""
        token = await self._get_valid_token(creds, client)
        res = await client.get(
            f"{GMAIL_API_BASE}/threads/{thread_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        )
        res.raise_for_status()
        raw = res.json()
        raw_messages = raw.get("messages", [])
        messages = [
            self._parse_message_resource(m)
            for m in raw_messages
            if isinstance(m, dict) and "id" in m
        ]
        subject = messages[0]["subject"] if messages else "No Subject"
        return {
            "thread_id": raw.get("id", thread_id),
            "subject": subject,
            "message_count": len(messages),
            "messages": messages,
        }

    async def sync_incremental(
        self,
        creds: OAuthCredentials,
        last_history_id: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Execute sync cycle:
        - If last_history_id provided, attempt history.list delta sync.
        - If history query fails (e.g. 404 expired history) or last_history_id is None,
          execute 7-day bounded fallback query.
        """
        http_client = client or httpx.AsyncClient(timeout=15.0)
        try:
            current_history_id = await self.get_profile_history_id(creds, http_client)

            if last_history_id:
                try:
                    token = await self._get_valid_token(creds, http_client)
                    res = await http_client.get(
                        f"{GMAIL_API_BASE}/history",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"startHistoryId": last_history_id, "maxResults": 100},
                    )
                    if res.status_code == 200:
                        data = res.json()
                        history_records = data.get("history", [])
                        message_ids = set()
                        for rec in history_records:
                            for msg in rec.get("messagesAdded", []):
                                message_ids.add(msg["message"]["id"])
                            for msg in rec.get("messages", []):
                                message_ids.add(msg["id"])

                        new_history_id = str(data.get("historyId", current_history_id))
                        items = []
                        for mid in list(message_ids)[:50]:
                            try:
                                detail = await self.fetch_message_detail(mid, creds, http_client)
                                items.append(detail)
                            except Exception:
                                pass
                        return items, new_history_id
                except Exception:
                    # HistoryId expired or invalid; proceed to 7-day fallback
                    pass

            # Fallback: 7-day bounded search
            token = await self._get_valid_token(creds, http_client)
            seven_days_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
            query = f"after:{seven_days_ago}"

            res = await http_client.get(
                f"{GMAIL_API_BASE}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "maxResults": 25},
            )
            res.raise_for_status()
            messages = res.json().get("messages", [])

            items = []
            for m in messages:
                try:
                    detail = await self.fetch_message_detail(m["id"], creds, http_client)
                    items.append(detail)
                except Exception:
                    pass

            return items, current_history_id
        finally:
            if client is None:
                await http_client.aclose()
