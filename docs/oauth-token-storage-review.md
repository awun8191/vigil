# OAuth and Token Storage Review

Follow-up to `connectors-cards-workflow-review.md`, restricted to the auth and
credential-at-rest story for Gmail, Linear, and Google Drive.

## 1. Verified current state

| Piece | State | Evidence |
|---|---|---|
| AES-256-GCM primitives | Implemented and correct: url-safe base64 `[12B nonce | ciphertext | 16B tag]`, constant-time tag failure, SHA-256 key derivation for non-hex keys. | `backend/src/config/security.py:87-131` |
| Master key | `SECRET_KEY` setting, 64-hex default. Production refuses the public default and the `.env.example` placeholder at boot. | `src/config/settings.py:45-48, 69-80` |
| Account schema | `connected_accounts` has `encrypted_credentials TEXT`, `scopes JSONB`, `status`, `enabled_tools`, unique `(user_id, connector_id, account_label)`. | `alembic/versions/0001_initial_schema.py:160-211` |
| Connect path | `POST /api/v1/connectors/accounts` takes `raw_credentials` as a dict **posted by the client** and encrypts it. There is no server-side authorization URL, no callback route, no `redirect_uri` anywhere in `backend/src`. | `src/modules/connectors/router.py:92-104`, `src/modules/connectors/data.py:8-13` |
| Response hygiene | `ConnectorAccountResponse` excludes credentials and labels status vocabulary `active / needs_reauth / error / disabled`. | `src/modules/connectors/data.py:16-26` |
| OAuth client code | `GmailAuthManager` has a complete authorization-URL builder, code-for-token exchange, and refresh. Never imported by the backend. Requests `readonly + compose + modify` with `offline + consent`. | `connectors/gmail/auth.py:81-154` |
| Google client credentials | No `GOOGLE_*` or `GMAIL_*` settings exist in the backend settings at all. | grep of `src/config/settings.py` returns nothing |

### 1.1 Findings

**F1, P0: the OAuth dance is client-mediated.** The current design has the
browser/app obtain the tokens and POST them to the backend in
`raw_credentials`. It works for the paired-device model, but it means every
device that calls the endpoint is a token exfiltration point, the app must
hold enough Google client material to build the consent URL, and the backend
cannot verify the grant actually belongs to the calling user (it trusts the
client's claim). The standard fix is server-side authorization code exchange:
backend builds the consent URL with a signed `state`, `/callback` exchanges
the code, tokens never transit the client. The machinery already exists in
`GmailAuthManager.get_authorization_url/exchange_code_for_tokens`; it just
needs routes and settings.

**F2, P1: two different AES-256-GCM key derivations.**
`GmailAuthManager.encrypt_credentials` derives the key as
`secret.encode().ljust(32, b"0")[:32]` (`connectors/gmail/auth.py:40-48`) while
the backend primitive derives via SHA-256 of the raw string
(`src/config/security.py:62-84`). The same `SECRET_KEY` produces two
incompatible ciphertext families. When the connector code lands in the
backend, every blob it encrypted under one derivation is unreadable under the
other. Keep exactly one primitive (`src/config/security.py`) and delete the
duplicate.

**F3, P1: access tokens are stored at rest for no benefit.** The blob layout
`{client_id, client_secret, access_token, refresh_token, token_expiry, email}`
(`connectors/gmail/auth.py:50-63`) encrypts a ~1-hour-lived access token
together with the long-lived refresh token. The access token is derivable on
demand with the refresh token; storing it only widens the blast radius of any
decryption. Store only the refresh token plus expiry metadata.

**F4, P1: app credentials inside every user blob.** `client_id` and
`client_secret` are properties of the Vigil Google Cloud project, not of any
user's grant. Today each account row would carry its own copy, so reading one
account's row yields the project's app secret too. Client credentials belong
in settings/env; the per-user blob holds only the grant.

**F5, P1: refresh is never persisted, and there is no key-version prefix.**
`refresh_access_token` mutates an in-memory dataclass
(`connectors/gmail/auth.py:129-154`) and `_get_valid_token` in the sync engine
calls it per request; nothing writes the rotated access token (or rotated
refresh token, which Google rotates on some events) back anywhere. On top,
the ciphertext carries no version marker, so rotating `SECRET_KEY` means a
one-way break: every stored blob becomes undecryptable with no path to
re-encrypt. Needed:

- `token_expires_at`, `last_refresh_error`, `last_refreshed_at` as plain
  columns beside the blob, so health queries never decrypt.
- An envelope prefix on `encrypted_credentials` (for example `v1:...`) so a
  rotation job can decrypt under the old key and re-encrypt under the new.

**F6, P2: the status vocabulary and the renderer already agree.**
`ConnectorAccountResponse.status` allows `needs_reauth / error / disabled`
(`src/modules/connectors/data.py:25`), and the widgets rail paints
`error / revoked / expired` as the failure tone (`WidgetsRail.tsx:32-37`). The
breadcrumb for a failed refresh is therefore already designed; the worker just
needs to write it.

**F7, P2: adjacent exposure beyond SECRET_KEY.** The production boot check
covers `SECRET_KEY` only. `DEVICE_TOKEN_SECRET` still ships a change-me
default (`src/config/settings.py:50`) and `CORS_ORIGINS` defaults to `["*"]`
(`settings.py:51`); neither is refused at boot for `APP_ENV=production`. Both
matter for an OAuth callback route, which a browser reaches directly.

**F8, note: Testing-mode refresh-token expiry applies to every grant.**
With the Google consent screen in Testing, refresh tokens expire in 7 days
and the app dies mid-week with `invalid_grant` and no user present. This hits
Gmail and Drive identically. Publish the app before real use; until then the
reconnect card flow is a weekly event, not an exception path.

## 2. Target design

### 2.1 Server-side authorization code flow (Gmail and Drive)

```
GET /api/v1/connectors/google/authorize?connector_id=gmail
    -> 302 accounts.google.com/o/oauth2/v2/auth
       client_id, redirect_uri, scope, access_type=offline,
       prompt=consent, state=<signed JWT {user_id, nonce, connector_id}>

GET /api/v1/connectors/google/callback?code&state
    -> verify state (HMAC via SECRET_KEY), consume nonce once (TTL 10 min)
    -> exchange code for tokens server-side
    -> store: refresh_token encrypted, scopes, token_expires_at
    -> 302 back to the web app  /settings?connect=gmail&status=ok
```

Concrete requirements:

1. New settings: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REDIRECT_URI`. Boot-fail in production when a connector is enabled
   but its client credentials are empty (mirror the existing
   `reject_unsafe_secret_key_in_production` pattern).
2. Scopes: Gmail `readonly + compose` (drop `modify`, it forces the full
   Google verification review). Drive `drive.metadata.readonly + drive.readonly`.
3. One-time `state` nonce stored in the DB with an expiry, so a callback URL
   cannot be replayed or attributed to a different user's session.
4. Audit: event on connect (exists), plus `connector_refresh_failed` and
   `connector_disconnect`, mirroring the existing no-credentials rule in
   `register_account` (`src/modules/connectors/service.py:195-209`).

### 2.2 Credential blob shape after the changes

```
encrypted_credentials = "v1:" + AES-256-GCM( { refresh_token, email } )
token_expires_at      TIMESTAMPTZ   -- next access-token expiry
last_refreshed_at     TIMESTAMPTZ
last_refresh_error    TEXT NULL
status                active | needs_reauth | error | disabled
```

- Access tokens are minted at call time from the refresh token and never
  stored; a rotated refresh token is re-encrypted and saved in the same
  transaction that emitted the API call.
- Client id/secret never enter the blob.
- Decryption lives in `ConnectorsService` only; no router returns a decrypted
  credential, and the existing response model stays secret-free.

### 2.3 Refresh lifecycle in the worker

1. Before each poll: decrypt refresh token, check `token_expires_at`.
2. Refresh if needed, write back the new blob + `token_expires_at` atomically.
3. On `invalid_grant`: set `status = needs_reauth`, record
   `last_refresh_error`, emit the audit event, and enqueue a reconnect card
   through the triage pipeline (path from the main review).
4. Disable-and-reconnect: `POST /connectors/accounts/{id}/revoke` deletes the
   blob (not just a flag), so "disconnected" never leaves recoverable
   + stale credentials in the row.

### 2.4 Linear

Linear does not need the Google machinery. Two acceptable shapes, in order:

- v1: a personal API key or manually minted bearer token stored the same way
  (encrypted blob, no access-token storage, and no expiry since Linear
  API keys are long-lived).
- later: OAuth 2.1 against `https://mcp.linear.app/mcp`. Note this is a token
  for an MCP transport, so the encrypted blob then feeds a per-request
  `Authorization` header on the Streamable HTTP client, which the MCP client
  currently has no field for (gap tracked in the main review).

## 3. Priority fix list

| # | Priority | Item |
|---|---|---|
| 1 | P0 | Server-side authorize/callback routes for Google with signed one-time `state`; drop client-mediated `raw_credentials`. |
| 2 | P0 | Add `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` settings with production boot gates; scopes `readonly+compose` (Gmail), `readonly` (Drive). |
| 3 | P1 | One AES-256-GCM primitive everywhere; delete the zero-pad derivation in `connectors/gmail/auth.py`. |
| 4 | P1 | Blob redesign: refresh token only, `v1:` envelope prefix, expiry/error columns beside it; never store access tokens. |
| 5 | P1 | Persist refreshes atomically in the poll worker; on `invalid_grant` set `needs_reauth` + reconnect card. |
| 6 | P2 | Revoke route deletes the blob; audit `connector_refresh_failed` / `connector_disconnect`. |
| 7 | P2 | Boot gates for `DEVICE_TOKEN_SECRET` and `CORS_ORIGINS` in production (callback route reaches browsers directly). |
| 8 | P2 | `SECRET_KEY` rotation job walking the `v1:`-prefixed blobs. |
| 9 | P2 | Publish the Google OAuth app to escape 7-day Testing-mode token expiry; treat reconnect cards as the interim UX. |

## 4. What to obtain, and where

| Item | Where to get it | URL |
|---|---|---|
| Google Cloud project | Create once; this is the app identity for Gmail + Drive. Existing project works too. | https://console.cloud.google.com/ |
| Enable Gmail API | APIs and Services, Library, search Gmail API, Enable. | https://console.cloud.google.com/apis/library/gmail.googleapis.com |
| Enable Drive API | Same flow, search Google Drive API, Enable. | https://console.cloud.google.com/apis/library/drive.googleapis.com |
| OAuth consent screen | Google Auth Platform, create app, External user type, fill app name + support email. Branding step required before clients can be created. | https://console.cloud.google.com/auth/branding |
| Test users (while unpublished) | Same consent screen, Audience / Test users, add the personal Gmail you will connect. Without this even the owner gets blocked. | https://console.cloud.google.com/auth/oss/branding |
| OAuth client ID + secret | Credentials, Create credentials, OAuth client ID, Web application. Authorized redirect URI: `http://localhost:8000/api/v1/connectors/google/callback` for dev, the production API host for prod. Copy client ID and secret into `.env`. | https://console.cloud.google.com/apis/credentials |
| Publish app (escape 7-day token expiry) | Consent screen, Publishing status, In production. Gmail restricted scopes will then request Google verification; for single-owner use the unverified production app still issues non-expiring refresh tokens to the test/owner account. | https://console.cloud.google.com/auth/branding |
| Verification reading (if verification ever matters) | Google's own OAuth verification process notes. | https://support.google.com/cloud/answer/13464321 |
| Manual testing of tokens (no code written yet) | OAuth 2.0 Playground with own credentials: gear icon, own client id/secret, request `gmail.readonly + gmail.compose`, exchange code, keep the refresh token. Proves the client config before any backend routes exist. | https://developers.google.com/oauthplayground |
| Gmail push notes (later, Pub/Sub, not v1) | Official push-configuration guide including the `users.watch` renewal cadence. | https://developers.google.com/gmail/api/guides/push |
| Linear API key | Account, Settings, Security and access, Personal API keys. Long-lived, paste into `.env`. | https://linear.app/settings/api |
| Linear OAuth apps (later path) | Same settings area, API, OAuth applications; playground and app creation live there. | https://linear.app/settings/api |
| Linear MCP specifics | Official docs: endpoint `https://mcp.linear.app/mcp`, bearer/API key auth, tool list. | https://linear.app/docs/mcp |
| Exa API key (optional) | Free tier needs no key; a key raises rate limits. Dashboard, sign up, create key. | https://dashboard.exa.ai/api-keys |
| Exa endpoint reference | The free MCP endpoint `https://mcp.exa.ai/mcp` is already wired and live-verified. | https://docs.exa.ai/reference/mcp-server |

Environment variables this produces, going in `backend/.env`:

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/connectors/google/callback
LINEAR_API_KEY=lin_api_...
EXA_API_KEY=            # optional, rate-limit raise only
```

Order of operations: project + two API enables + consent screen + test user first, then the client ID/secret, then everything else is code-side. The OAuth Playground pass costs ten minutes and turns any consent-screen misconfiguration into a visible error before it reaches the implementation.
