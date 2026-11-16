"""Tests for multi-question ask_user, argument-mutating approvals, raise_card, rich Gmail MIME, and free MCP tools."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from connectors.gmail.auth import GmailAuthManager, OAuthCredentials
from connectors.gmail.server import GmailMCPServer, markdown_to_email_html
from src.config.settings import Settings
from src.integrations.mcp.mcp import MCPToolDefinition
from src.modules.approvals.service import ApprovalsService
from src.modules.cards.service import CardsService
from src.modules.runs.repository import AgentConfigDTO, RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal
from src.modules.runs.tools import ToolContext, invoke_tool
from src.modules.runs.tools.mcp_bridge import MCPToolAdapterFactory
from tests.unit.loop_fakes import FakeToolExecutor, ScriptedModel, agent_config, text_response, tool_response


@pytest.mark.asyncio
async def test_ask_user_multi_question_and_loop_answer_forwarding() -> None:
    """ask_user accepts stacked questions and RunLoop forwards user answers on resume."""
    model = ScriptedModel(
        [
            tool_response(
                (
                    "call-ask-1",
                    "ask_user",
                    {
                        "context": "Clarifying email tone and recipients",
                        "questions": [
                            {
                                "id": "tone",
                                "header": "Tone",
                                "question": "Which tone should I use?",
                                "options": [
                                    {"label": "Concise & Direct (Recommended)", "description": "2-3 sentences"},
                                    {"label": "Warm & Detailed", "description": "Full context"},
                                ],
                                "multi_select": False,
                            },
                            {
                                "id": "recipients",
                                "header": "Recipients",
                                "question": "Who should be included?",
                                "options": ["Boardy only", "Boardy + Shubham"],
                                "multi_select": True,
                            },
                        ],
                    },
                )
            ),
            text_response("Got it, drafting a concise reply to Boardy only."),
        ]
    )
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    cfg = agent_config()
    await journal.save_config(cfg)
    run = await service.create_run(RunCreateDTO(config_id=cfg.id))

    paused = await service.process(run.id)
    assert paused.status == "awaiting_approval"

    approvals = await journal.list_approvals(run.id)
    assert len(approvals) == 1
    assert approvals[0].tool_name == "ask_user"

    answers = {"Tone": "Concise & Direct (Recommended)", "Recipients": ["Boardy only"]}
    await journal.decide_approval(
        approvals[0].id,
        status="approved",
        note=json.dumps({"answers": answers}),
    )

    resumed = await service.process(run.id)
    assert resumed.status == "completed"

    messages = await journal.list_messages(run.id)
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0].content or "{}")
    assert payload["ok"] is True
    assert payload["content"]["status"] == "answered"
    assert payload["content"]["answers"] == answers


@pytest.mark.asyncio
async def test_approval_with_updated_arguments_forwards_edited_email() -> None:
    """User-edited email arguments on approval override the original tool arguments."""
    model = ScriptedModel(
        [
            tool_response(
                (
                    "call-send-1",
                    "gmail_send",
                    {
                        "to": ["boardy@boardy.ai", "shubham@example.com"],
                        "subject": "Re: Intro",
                        "body": "Original draft",
                    },
                )
            ),
            text_response("Email sent with your edits."),
        ]
    )
    tools = FakeToolExecutor()
    journal = InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    cfg = agent_config()
    await journal.save_config(cfg)
    run = await service.create_run(RunCreateDTO(config_id=cfg.id))

    paused = await service.process(run.id)
    assert paused.status == "awaiting_approval"

    approvals = await journal.list_approvals(run.id)
    approval_id = approvals[0].id

    # Simulate ApprovalsService updating arguments on approval
    edited_args = {
        "to": ["boardy@boardy.ai"],
        "subject": "Re: Intro (Updated)",
        "body": "**Updated** rich markdown body",
    }
    row = journal.approvals[approval_id]
    from dataclasses import replace

    journal.approvals[approval_id] = replace(
        row,
        status="approved",
        arguments=edited_args,
        decided_at=datetime.now(timezone.utc),
    )

    resumed = await service.process(run.id)
    assert resumed.status == "completed"
    assert tools.external == [("gmail_send", edited_args)]


@pytest.mark.asyncio
async def test_raise_card_tool_creates_hark_pro_card(tmp_path: Path) -> None:
    """raise_card creates a card scoped to context.user_id with Hark Pro verb and actions."""
    CardsService._cards.clear()
    user_id = uuid4()
    ctx = ToolContext(
        runner=SimpleNamespace(),  # type: ignore[arg-type]
        container_id="host-only",
        delivery_dir=tmp_path,
        user_id=user_id,
        session=None,
    )
    res = await invoke_tool(
        "raise_card",
        {
            "title": "Ask Boardy to move Tuesday 9 AM intro",
            "summary": "Conflicts with sprint planning.",
            "type": "email",
            "urgency": "high",
            "verb": "Draft",
            "thread_id": "th_boardy_1",
        },
        ctx,
    )
    assert res.ok is True
    assert res.content["verb"] == "Draft"
    cards = await CardsService(session=None, settings=Settings(APP_ENV="test")).list_cards_by_user(user_id)
    assert len(cards) == 1
    assert cards[0].title == "Ask Boardy to move Tuesday 9 AM intro"
    assert cards[0].actions[0].action_type == "run"


def test_gmail_oauth_state_roundtrip_and_markdown_html() -> None:
    """GmailAuthManager signs/verifies OAuth state and markdown_to_email_html renders rich HTML."""
    mgr = GmailAuthManager("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    uid = str(uuid4())
    state = mgr.create_oauth_state(uid, connector_id="gmail")
    parsed = mgr.verify_oauth_state(state)
    assert parsed["uid"] == uid
    assert parsed["cid"] == "gmail"

    html_out = markdown_to_email_html("Hello **Team**,\n\n- Item 1\n- Item 2")
    assert "<strong>Team</strong>" in html_out
    assert "<ul" in html_out and "<li" in html_out


@pytest.mark.asyncio
async def test_gmail_read_thread_and_update_draft() -> None:
    """GmailMCPServer supports gmail_read_thread and gmail_update_draft."""
    mgr = GmailAuthManager("secret")
    creds = OAuthCredentials(
        client_id="cid",
        client_secret="csec",
        access_token="tok",
        refresh_token="ref",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    server = GmailMCPServer(mgr, creds)

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/threads/th_1" in url:
            return httpx.Response(
                200,
                json={
                    "id": "th_1",
                    "messages": [
                        {
                            "id": "m1",
                            "threadId": "th_1",
                            "snippet": "Hi",
                            "payload": {
                                "headers": [
                                    {"name": "Subject", "value": "Sync"},
                                    {"name": "From", "value": "alice@example.com"},
                                    {"name": "Message-ID", "value": "<m1@mail.gmail.com>"},
                                ]
                            },
                        }
                    ],
                },
            )
        if "/drafts/d_1" in url and req.method == "PUT":
            return httpx.Response(200, json={"id": "d_1", "message": {"id": "m_d1", "threadId": "th_1"}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        thread = await server.execute_tool("gmail_read_thread", {"thread_id": "th_1"}, client=client)
        assert thread["message_count"] == 1
        assert thread["messages"][0]["message_id_header"] == "<m1@mail.gmail.com>"

        updated = await server.execute_tool(
            "gmail_update_draft",
            {"draft_id": "d_1", "to": ["alice@example.com"], "subject": "Re: Sync", "body": "**Updated**"},
            client=client,
        )
        assert updated["status"] == "draft_updated"
        assert "<strong>Updated</strong>" in updated["body_html"]


def test_runs_service_allows_read_and_local_mcp_tools_freely() -> None:
    """Read/local MCP tools partition to 'local' while external MCP tools stay 'external'."""
    search_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="gmail_search", level="read"),
        lambda n, a: ("ok", False),
        level="read",
    )
    draft_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="gmail_create_draft", level="read"),
        lambda n, a: ("ok", False),
        level="read",
    )
    send_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="gmail_send", level="external"),
        lambda n, a: ("ok", False),
        level="external",
    )

    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        mcp_tools=[search_tool, draft_tool, send_tool],
    )
    cfg = AgentConfigDTO(
        id=uuid4(),
        name="default",
        system_prompt="test",
        model="gpt-4o",
        tools=["read"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={},
        limits={"max_cost_micros": 0},
    )
    policy = service.resolve_partition_policy(cfg)
    assert policy.partition_for("gmail_search") == "local"
    assert policy.partition_for("gmail_create_draft") == "local"
    assert policy.partition_for("gmail_send") == "external"
