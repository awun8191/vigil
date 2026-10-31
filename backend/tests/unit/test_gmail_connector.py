"""Unit tests for standalone Gmail MCP connector: auth encryption, sync engine, and tool execution."""
from datetime import datetime, timezone, timedelta
import pytest
import httpx

from connectors.gmail.auth import GmailAuthManager, OAuthCredentials
from connectors.gmail.server import GmailMCPServer
from connectors.gmail.sync import GmailSyncEngine


SAMPLE_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def test_gmail_auth_credential_encryption_roundtrip() -> None:
    """Verify OAuth credentials are encrypted and decrypted with AES-256-GCM."""
    mgr = GmailAuthManager(secret_key=SAMPLE_SECRET_KEY)
    creds = OAuthCredentials(
        client_id="test_client_id.apps.googleusercontent.com",
        client_secret="test_client_secret_xyz",
        access_token="ya29.test_access_token_abc",
        refresh_token="1//test_refresh_token_def",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        email="user@example.com",
    )

    encrypted = mgr.encrypt_credentials(creds)
    assert isinstance(encrypted, str)
    assert len(encrypted) > 30

    decrypted = mgr.decrypt_credentials(encrypted)
    assert decrypted.client_id == creds.client_id
    assert decrypted.client_secret == creds.client_secret
    assert decrypted.access_token == creds.access_token
    assert decrypted.refresh_token == creds.refresh_token
    assert decrypted.email == creds.email


def test_gmail_consent_url_generation() -> None:
    """Verify authorization consent URL contains required parameters and scopes."""
    mgr = GmailAuthManager(secret_key=SAMPLE_SECRET_KEY)
    url = mgr.get_authorization_url(
        client_id="my-client-id",
        redirect_uri="https://api.virgil.local/oauth/callback",
        state="state-12345",
    )
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in url
    assert "client_id=my-client-id" in url
    assert "response_type=code" in url
    assert "access_type=offline" in url
    assert "gmail.readonly" in url


def test_gmail_mcp_tool_definitions() -> None:
    """Verify Gmail MCP tool definitions and approval policies."""
    mgr = GmailAuthManager(secret_key=SAMPLE_SECRET_KEY)
    server = GmailMCPServer(auth_manager=mgr)

    tools = server.list_tools()
    tool_names = [t["name"] for t in tools]
    assert "gmail_search" in tool_names
    assert "gmail_read" in tool_names
    assert "gmail_create_draft" in tool_names
    assert "gmail_send" in tool_names

    # Check security approval policies
    assert server.get_tool_approval_level("gmail_search") == "read"
    assert server.get_tool_approval_level("gmail_read") == "read"
    assert server.get_tool_approval_level("gmail_create_draft") == "read"
    assert server.get_tool_approval_level("gmail_send") == "external"


@pytest.mark.asyncio
async def test_gmail_mcp_tool_execution() -> None:
    """Verify mock execution of MCP tools: search, read, create_draft, send."""
    mgr = GmailAuthManager(secret_key=SAMPLE_SECRET_KEY)
    creds = OAuthCredentials(
        client_id="client_id",
        client_secret="client_secret",
        access_token="valid_access_token",
        refresh_token="valid_refresh_token",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    server = GmailMCPServer(auth_manager=mgr, credentials=creds)

    # Mock HTTP transport simulating Google API
    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/messages?" in url or url.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "msg_001", "threadId": "th_001"}]})
        elif "/messages/msg_001" in url:
            return httpx.Response(200, json={
                "id": "msg_001",
                "threadId": "th_001",
                "snippet": "Action required: review deployment",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Deployment Review Required"},
                        {"name": "From", "value": "lead@company.com"},
                        {"name": "To", "value": "me@company.com"},
                        {"name": "Date", "value": "Sat, 31 Oct 2026 12:00:00 +0000"},
                    ],
                },
            })
        elif "/drafts" in url:
            return httpx.Response(200, json={"id": "draft_001", "message": {"id": "msg_draft_001"}})
        elif "/messages/send" in url:
            return httpx.Response(200, json={"id": "msg_sent_001", "threadId": "th_001"})
        elif "/profile" in url:
            return httpx.Response(200, json={"historyId": "99999"})
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as mock_client:
        # 1. Search tool
        res_search = await server.execute_tool("gmail_search", {"query": "is:unread"}, client=mock_client)
        assert "messages" in res_search
        assert len(res_search["messages"]) == 1
        assert res_search["messages"][0]["id"] == "msg_001"

        # 2. Read tool
        res_read = await server.execute_tool("gmail_read", {"message_id": "msg_001"}, client=mock_client)
        assert res_read["id"] == "msg_001"
        assert res_read["subject"] == "Deployment Review Required"
        assert res_read["sender"] == "lead@company.com"

        # 3. Create Draft tool
        res_draft = await server.execute_tool(
            "gmail_create_draft",
            {"to": ["lead@company.com"], "subject": "Re: Deployment Review", "body": "Draft looks good."},
            client=mock_client,
        )
        assert res_draft["status"] == "draft_created"
        assert res_draft["draft_id"] == "draft_001"

        # 4. Send tool
        res_send = await server.execute_tool(
            "gmail_send",
            {"to": ["lead@company.com"], "subject": "Approved", "body": "Approved and signed."},
            client=mock_client,
        )
        assert res_send["status"] == "sent"
        assert res_send["message_id"] == "msg_sent_001"
