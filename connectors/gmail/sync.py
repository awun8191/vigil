"""Gmail synchronization engine supporting historyId delta sync and 7-day bounded fallback."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
import httpx

from connectors.gmail.auth import OAuthCredentials, GmailAuthManager


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


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

    async def fetch_message_detail(
        self,
        message_id: str,
        creds: OAuthCredentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Fetch full message details including headers and snippet."""
        token = await self._get_valid_token(creds, client)
        res = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        )
        res.raise_for_status()
        raw = res.json()

        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "No Subject")
        sender = headers.get("from", "Unknown")
        to = headers.get("to", "")
        date_str = headers.get("date", "")

        # Extract body text
        body = raw.get("snippet", "")
        payload = raw.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                    import base64
                    try:
                        decoded = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                        body = decoded
                        break
                    except Exception:
                        pass

        return {
            "id": raw["id"],
            "thread_id": raw.get("threadId", raw["id"]),
            "subject": subject,
            "sender": sender,
            "to": to,
            "date": date_str,
            "snippet": raw.get("snippet", ""),
            "body": body,
            "history_id": raw.get("historyId"),
            "labels": raw.get("labelIds", []),
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
