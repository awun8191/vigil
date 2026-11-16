"""Google OAuth2 token management and AES-256-GCM credential persistence for Gmail MCP connector."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
from typing import Any
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.security import decrypt_secret, encrypt_secret


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expiry": self.token_expiry.isoformat(),
            "email": self.email,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        default_client_id: str = "",
        default_client_secret: str = "",
    ) -> "OAuthCredentials":
        raw_expiry = data.get("token_expiry")
        if isinstance(raw_expiry, str) and raw_expiry:
            try:
                expiry = datetime.fromisoformat(raw_expiry)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except ValueError:
                expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        else:
            expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        return cls(
            client_id=str(data.get("client_id") or default_client_id),
            client_secret=str(data.get("client_secret") or default_client_secret),
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            token_expiry=expiry,
            email=data.get("email"),
        )


class GmailAuthManager:
    """Manages Google OAuth2 tokens and AES-256-GCM encrypted persistence."""

    def __init__(self, secret_key: str | bytes) -> None:
        self._raw_secret = secret_key
        if isinstance(secret_key, str):
            if len(secret_key) == 64:
                try:
                    self._key = bytes.fromhex(secret_key)
                except ValueError:
                    self._key = hashlib.sha256(secret_key.encode("utf-8")).digest()
            else:
                self._key = hashlib.sha256(secret_key.encode("utf-8")).digest()
        else:
            self._key = (
                secret_key[:32]
                if len(secret_key) == 32
                else hashlib.sha256(secret_key).digest()
            )
        self._aesgcm = AESGCM(self._key)

    def encrypt_credentials(self, creds: OAuthCredentials) -> str:
        """Encrypt OAuthCredentials into AES-256-GCM URL-safe base64 string."""
        plaintext = json.dumps(creds.to_dict())
        return encrypt_secret(plaintext, self._raw_secret)

    def decrypt_credentials(
        self,
        encrypted_b64: str,
        *,
        default_client_id: str = "",
        default_client_secret: str = "",
    ) -> OAuthCredentials:
        """Decrypt AES-256-GCM base64 string into OAuthCredentials."""
        try:
            plaintext = decrypt_secret(encrypted_b64, self._raw_secret)
            data = json.loads(plaintext)
        except Exception:
            raw = base64.b64decode(encrypted_b64)
            nonce = raw[:12]
            ciphertext = raw[12:]
            plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, None)
            data = json.loads(plaintext_bytes.decode("utf-8"))

        return OAuthCredentials.from_dict(
            data,
            default_client_id=default_client_id,
            default_client_secret=default_client_secret,
        )

    def create_oauth_state(
        self,
        user_id: str,
        connector_id: str = "gmail",
        ttl_seconds: int = 600,
    ) -> str:
        """Create an HMAC-signed OAuth2 state token bound to user_id and connector_id."""
        exp = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
        nonce = base64.urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("=")
        payload = {"uid": str(user_id), "cid": connector_id, "nonce": nonce, "exp": exp}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
        sig = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_oauth_state(self, state_token: str) -> dict[str, Any]:
        """Verify an HMAC-signed OAuth2 state token and return its payload."""
        if "." not in state_token:
            raise ValueError("Malformed OAuth state token")
        body, sig = state_token.rsplit(".", 1)
        expected_sig = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid OAuth state signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if int(datetime.now(timezone.utc).timestamp()) > exp:
            raise ValueError("Expired OAuth state token")
        return payload

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
            access_token = data["access_token"]
            email: str | None = None
            try:
                prof_res = await client.get(
                    GMAIL_PROFILE_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if prof_res.status_code == 200:
                    email = prof_res.json().get("emailAddress")
            except Exception:
                email = None

            return OAuthCredentials(
                client_id=client_id,
                client_secret=client_secret,
                access_token=access_token,
                refresh_token=data.get("refresh_token", ""),
                token_expiry=expiry,
                email=email,
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
