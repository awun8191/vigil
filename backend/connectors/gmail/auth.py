"""Google OAuth2 token management and AES-256-GCM credential persistence for Gmail MCP connector."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import os
from typing import Any
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class OAuthCredentials:
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    token_expiry: datetime
    email: str | None = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.token_expiry


class GmailAuthManager:
    """Manages Google OAuth2 tokens and AES-256-GCM encrypted persistence."""

    def __init__(self, secret_key: str | bytes) -> None:
        if isinstance(secret_key, str):
            if len(secret_key) == 64:
                self._key = bytes.fromhex(secret_key)
            else:
                self._key = secret_key.encode("utf-8").ljust(32, b"\0")[:32]
        else:
            self._key = secret_key[:32]
        self._aesgcm = AESGCM(self._key)

    def encrypt_credentials(self, creds: OAuthCredentials) -> str:
        """Encrypt OAuthCredentials into AES-256-GCM base64 string."""
        data = {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "token_expiry": creds.token_expiry.isoformat(),
            "email": creds.email,
        }
        raw_bytes = json.dumps(data).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, raw_bytes, None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt_credentials(self, encrypted_b64: str) -> OAuthCredentials:
        """Decrypt AES-256-GCM base64 string into OAuthCredentials."""
        raw = base64.b64decode(encrypted_b64)
        nonce = raw[:12]
        ciphertext = raw[12:]
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        data = json.loads(plaintext.decode("utf-8"))
        return OAuthCredentials(
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_expiry=datetime.fromisoformat(data["token_expiry"]),
            email=data.get("email"),
        )

    def get_authorization_url(self, client_id: str, redirect_uri: str, state: str) -> str:
        """Generate OAuth2 consent URL."""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        query = "&".join(f"{k}={httpx.URL('', params={k: v}).query.decode('utf-8')}" for k, v in params.items())
        return f"{GOOGLE_AUTH_URL}?{query}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> OAuthCredentials:
        """Exchange authorization code for access and refresh tokens."""
        payload = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        client = http_client or httpx.AsyncClient(timeout=10.0)
        try:
            res = await client.post(GOOGLE_TOKEN_URL, data=payload)
            res.raise_for_status()
            data = res.json()
            expires_in = data.get("expires_in", 3600)
            expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
            return OAuthCredentials(
                client_id=client_id,
                client_secret=client_secret,
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                token_expiry=expiry,
            )
        finally:
            if http_client is None:
                await client.aclose()

    async def refresh_access_token(
        self,
        creds: OAuthCredentials,
        http_client: httpx.AsyncClient | None = None,
    ) -> OAuthCredentials:
        """Refresh expired access token using refresh_token."""
        payload = {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "grant_type": "refresh_token",
        }
        client = http_client or httpx.AsyncClient(timeout=10.0)
        try:
            res = await client.post(GOOGLE_TOKEN_URL, data=payload)
            res.raise_for_status()
            data = res.json()
            expires_in = data.get("expires_in", 3600)
            creds.access_token = data["access_token"]
            creds.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
            if "refresh_token" in data:
                creds.refresh_token = data["refresh_token"]
            return creds
        finally:
            if http_client is None:
                await client.aclose()
