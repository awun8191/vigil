"""
Adversarial challenge test suite for Milestone 3 router factories and dependency injection.

Adversarially tests:
1. FastAPI dependency_overrides swapping: instantiate create_app(), override domain
   service dependencies with mock implementations satisfying repository protocols,
   and verify endpoint routes receive the mock data.
2. Factory isolation: verify router instances cannot be mutated globally across separate
   create_app() calls or separate builder invocations.
3. Invalid payload submissions to domain module routes to verify proper 422 error
   serialization adhering to {"code", "message", "request_id", "details"}.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import re
from typing import Any
from uuid import UUID, uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.router import MODULE_FACTORIES
from src.config.settings import Settings
from src.dependencies import (
    get_approvals_service,
    get_cards_service,
    get_chat_service,
    get_health_service,
    get_runs_service,
)
from src.main import create_app
from src.modules.approvals.repository import (
    ApprovalCreateDTO,
    ApprovalDTO,
    ApprovalsRepository,
)
from src.modules.cards.repository import (
    CardActionDTO,
    CardCreateDTO,
    CardDTO,
    CardsRepository,
    CardUpdateDTO,
)
from src.modules.chat.repository import (
    ChatMessageCreateDTO,
    ChatMessageDTO,
    ChatRepository,
    ChatSessionDTO,
)
from src.modules.health.repository import HealthCheckDTO, HealthRepository
from src.modules.runs.repository import (
    AgentConfigDTO,
    MessageCreateDTO,
    MessageDTO,
    RunCreateDTO,
    RunDTO,
    RunsRepository,
    RunStepCreateDTO,
    RunStepDTO,
)

PROCESS_TIME_REGEX = re.compile(r"^\d+\.\d{6}s$")


# =============================================================================
# Mock Implementations Strictly Satisfying Repository Protocols
# =============================================================================

class MockHealthRepository:
    """Mock implementation satisfying @runtime_checkable HealthRepository protocol."""

    def __init__(self, version: str = "9.9.9-mocked", db_connected: bool = True) -> None:
        self.version = version
        self.db_connected = db_connected

    async def check_database(self) -> bool:
        return self.db_connected

    async def get_system_status(self) -> HealthCheckDTO:
        return HealthCheckDTO(
            status="ok" if self.db_connected else "degraded",
            version=self.version,
            timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            database_connected=self.db_connected,
            details={"mock_provider": "MockHealthRepository", "injected": True},
        )


class MockCardsRepository:
    """Mock implementation satisfying @runtime_checkable CardsRepository protocol."""

    def __init__(self) -> None:
        self.created_cards: list[CardDTO] = []
        self.mock_card = CardDTO(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            item_id=None,
            thread_id="mock_thread_42",
            type="billing",
            urgency="critical",
            title="Mock Injected Card",
            summary="This is mock injected data for DI verification.",
            due_at=None,
            status="new",
            actions=[],
            feedback=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def get_card_by_id(self, card_id: UUID) -> CardDTO | None:
        if card_id == self.mock_card.id:
            return self.mock_card
        return None

    async def list_cards_by_user(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CardDTO]:
        return [self.mock_card]

    async def create_card(self, data: CardCreateDTO) -> CardDTO:
        created = CardDTO(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            user_id=data.user_id,
            item_id=data.item_id,
            thread_id=data.thread_id,
            type=data.type,
            urgency=data.urgency,
            title=f"MOCK_CREATED: {data.title}",
            summary=data.summary,
            due_at=data.due_at,
            status=data.status,
            actions=data.actions,
            feedback=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.created_cards.append(created)
        return created

    async def update_card(self, card_id: UUID, data: CardUpdateDTO) -> CardDTO | None:
        return self.mock_card

    async def get_by_thread_id(self, thread_id: str) -> CardDTO | None:
        return self.mock_card

    async def record_feedback(self, card_id: UUID, feedback: str) -> CardDTO | None:
        return self.mock_card

    async def count_cards(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
    ) -> int:
        return 999


class MockChatRepository:
    """Mock implementation satisfying @runtime_checkable ChatRepository protocol."""

    def __init__(self) -> None:
        self.mock_session = ChatSessionDTO(
            session_id=UUID("33333333-3333-3333-3333-333333333333"),
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            title="Mock Injected Chat Session",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            message_count=5,
        )

    async def get_session(self, session_id: UUID) -> ChatSessionDTO | None:
        return self.mock_session

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionDTO]:
        return [self.mock_session]

    async def create_session(
        self,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSessionDTO:
        return self.mock_session

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        return ChatMessageDTO(
            id=uuid4(),
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            created_at=datetime.now(timezone.utc),
        )

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        return [
            ChatMessageDTO(
                id=uuid4(),
                session_id=session_id,
                role="assistant",
                content="Mock reply from injected ChatRepository",
                created_at=datetime.now(timezone.utc),
            )
        ]

    async def delete_session(self, session_id: UUID) -> bool:
        return True


class MockRunsRepository:
    """Mock implementation satisfying @runtime_checkable RunsRepository protocol."""

    def __init__(self) -> None:
        self.mock_run = RunDTO(
            id=UUID("44444444-4444-4444-4444-444444444444"),
            config_id=UUID("55555555-5555-5555-5555-555555555555"),
            card_id=None,
            parent_run_id=None,
            depth=0,
            status="running",
            started_at=datetime.now(timezone.utc),
            ended_at=None,
            token_usage={"total": 500},
            cost_usd=0.015,
        )

    async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
        return self.mock_run

    async def latest_run_for_session(self, session_id: UUID) -> RunDTO | None:
        return self.mock_run

    async def create_run(self, data: RunCreateDTO) -> RunDTO:
        return self.mock_run

    async def update_run_status(
        self,
        run_id: UUID,
        status: str,
        ended_at: datetime | None = None,
        token_usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> RunDTO:
        return self.mock_run

    async def save_message(self, data: MessageCreateDTO) -> MessageDTO:
        return MessageDTO(
            id=uuid4(),
            run_id=data.run_id,
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            tool_calls=data.tool_calls,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            tool_arguments=data.tool_arguments,
            tool_result_reference=data.tool_result_reference,
            created_at=datetime.now(timezone.utc),
        )

    async def get_messages_for_run(self, run_id: UUID) -> list[MessageDTO]:
        return []

    async def save_run_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        return RunStepDTO(
            id=uuid4(),
            run_id=data.run_id,
            step_number=data.step_number,
            event_type=data.event_type,
            payload=data.payload,
            created_at=datetime.now(timezone.utc),
        )

    async def get_steps_since(self, run_id: UUID, step_number: int) -> list[RunStepDTO]:
        return []

    async def get_agent_config(self, config_id: UUID) -> AgentConfigDTO | None:
        return None

    async def get_agent_config_by_name(self, name: str) -> AgentConfigDTO | None:
        return None


class MockApprovalsRepository:
    """Mock implementation satisfying @runtime_checkable ApprovalsRepository protocol."""

    def __init__(self) -> None:
        self.mock_approval = ApprovalDTO(
            id=UUID("66666666-6666-6666-6666-666666666666"),
            run_id=UUID("44444444-4444-4444-4444-444444444444"),
            tool_call_id="call_mock_123",
            tool_name="send_wire_transfer",
            arguments={"recipient": "Vendor A", "amount": 50000},
            status="pending",
            decided_at=None,
            decision_note=None,
            created_at=datetime.now(timezone.utc),
        )

    async def get_approval_by_id(self, approval_id: UUID) -> ApprovalDTO | None:
        return self.mock_approval

    async def list_pending_approvals(self, limit: int = 50, offset: int = 0) -> list[ApprovalDTO]:
        return [self.mock_approval]

    async def list_approvals_by_run(self, run_id: UUID) -> list[ApprovalDTO]:
        return [self.mock_approval]

    async def create_approval(self, data: ApprovalCreateDTO) -> ApprovalDTO:
        return self.mock_approval

    async def update_decision(
        self,
        approval_id: UUID,
        status: str,
        decided_at: datetime,
        decision_note: str | None = None,
    ) -> ApprovalDTO | None:
        return self.mock_approval

    async def get_by_run_and_tool_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> ApprovalDTO | None:
        return self.mock_approval


# =============================================================================
# 1. Verification of Protocol Duck Typing on Mocks
# =============================================================================

def test_mocks_satisfy_runtime_checkable_protocols() -> None:
    """Verify that all mock instances strictly satisfy their respective @runtime_checkable protocols."""
    assert isinstance(MockHealthRepository(), HealthRepository)
    assert isinstance(MockCardsRepository(), CardsRepository)
    assert isinstance(MockChatRepository(), ChatRepository)
    assert isinstance(MockRunsRepository(), RunsRepository)
    assert isinstance(MockApprovalsRepository(), ApprovalsRepository)


# =============================================================================
# 2. Dependency Overrides Swapping Adversarial Verification
# =============================================================================

@pytest.mark.asyncio
async def test_fastapi_dependency_overrides_swapping() -> None:
    """
    Instantiate create_app(), override domain service dependencies with mock implementations
    satisfying repository protocols, and verify endpoint requests receive the mock data.
    """
    app = create_app()

    mock_health = MockHealthRepository(version="8.8.8-custom-override", db_connected=False)
    mock_cards = MockCardsRepository()
    mock_chat = MockChatRepository()
    mock_runs = MockRunsRepository()
    mock_approvals = MockApprovalsRepository()

    app.dependency_overrides[get_health_service] = lambda: mock_health
    app.dependency_overrides[get_cards_service] = lambda: mock_cards
    app.dependency_overrides[get_chat_service] = lambda: mock_chat
    app.dependency_overrides[get_runs_service] = lambda: mock_runs
    app.dependency_overrides[get_approvals_service] = lambda: mock_approvals

    auth_headers = {"Authorization": "Bearer vgt_test_override_token"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Health endpoint returns mock health data
        res_health = await client.get("/api/v1/health")
        assert res_health.status_code == 200
        health_data = res_health.json()
        assert health_data["version"] == "8.8.8-custom-override"
        assert health_data["status"] == "degraded"
        assert health_data["database"] == "error"
        assert health_data["details"]["mock_provider"] == "MockHealthRepository"

        # B. Cards endpoint returns mock cards data
        res_cards = await client.get("/api/v1/cards", headers=auth_headers)
        assert res_cards.status_code == 200
        cards_data = res_cards.json()
        assert cards_data["total"] == 999
        assert len(cards_data["items"]) == 1
        assert cards_data["items"][0]["title"] == "Mock Injected Card"
        assert cards_data["items"][0]["id"] == str(mock_cards.mock_card.id)

        # C. Cards POST creates via mock repository
        res_post_card = await client.post(
            "/api/v1/cards",
            json={"title": "Test Creation", "type": "billing", "urgency": "critical"},
            headers=auth_headers,
        )
        assert res_post_card.status_code == 201
        assert res_post_card.json()["title"] == "MOCK_CREATED: Test Creation"
        assert len(mock_cards.created_cards) == 1

        # D. Chat sessions returns mock chat data
        res_chat = await client.get("/api/v1/chat/sessions", headers=auth_headers)
        assert res_chat.status_code == 200
        chat_data = res_chat.json()
        assert len(chat_data) == 1
        assert chat_data[0]["title"] == "Mock Injected Chat Session"
        assert chat_data[0]["session_id"] == str(mock_chat.mock_session.session_id)

        # E. Runs endpoint returns mock run
        res_run = await client.get(
            f"/api/v1/runs/{mock_runs.mock_run.id}",
            headers=auth_headers,
        )
        assert res_run.status_code == 200
        assert res_run.json()["id"] == str(mock_runs.mock_run.id)
        assert res_run.json()["cost_usd"] == 0.015

        # F. Approvals endpoint returns mock pending approval
        res_appr = await client.get("/api/v1/approvals", headers=auth_headers)
        assert res_appr.status_code == 200
        assert len(res_appr.json()) == 1
        assert res_appr.json()[0]["tool_name"] == "send_wire_transfer"


# =============================================================================
# 3. Router Factory Isolation Across Separate App Instances
# =============================================================================

def test_router_factory_isolation_no_global_state_leakage() -> None:
    """
    Test that routers cannot be mutated globally across separate create_app() calls
    or across individual module factory invocations.
    """
    settings = Settings()
    app1 = create_app(settings)
    app2 = create_app(settings)

    # 1. Apps are distinct
    assert app1 is not app2
    assert app1.router is not app2.router
    assert app1.dependency_overrides is not app2.dependency_overrides

    # 2. Every domain router factory generates independent router and route instances
    for mod_name, factory_name in MODULE_FACTORIES:
        mod = importlib.import_module(f"src.modules.{mod_name}.router")
        factory = getattr(mod, factory_name)
        r1 = factory(settings)
        r2 = factory(settings)

        assert r1 is not r2, f"{mod_name} factory returned the same APIRouter instance!"
        assert r1.routes is not r2.routes, f"{mod_name} factory shared the routes list!"

        # Mutate r1 by injecting a rogue route
        r1.add_api_route("/adversarial_mutation_probe", lambda: {"injected": True})

        # Verify r1 has it, r2 is completely unaffected
        assert any(route.path.endswith("/adversarial_mutation_probe") for route in r1.routes)
        assert not any(route.path.endswith("/adversarial_mutation_probe") for route in r2.routes)


@pytest.mark.asyncio
async def test_app_isolation_under_adversarial_mutation() -> None:
    """
    Mutate app1 by adding rogue routes and dependency overrides.
    Verify app2 remains completely insulated and returns 404 for app1's injected route.
    """
    app1 = create_app()
    app2 = create_app()

    # Inject rogue route into app1
    @app1.get("/api/v1/rogue_injected_probe")
    def rogue_route():
        return {"compromised": True}

    # Inject dependency override into app1
    mock_health = MockHealthRepository(version="compromised-v1")
    app1.dependency_overrides[get_health_service] = lambda: mock_health

    t1 = ASGITransport(app=app1, raise_app_exceptions=False)
    t2 = ASGITransport(app=app2, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=t1, base_url="http://test") as c1:
        res1_rogue = await c1.get("/api/v1/rogue_injected_probe")
        assert res1_rogue.status_code == 200
        assert res1_rogue.json() == {"compromised": True}

        res1_health = await c1.get("/api/v1/health")
        assert res1_health.json()["version"] == "compromised-v1"

    async with httpx.AsyncClient(transport=t2, base_url="http://test") as c2:
        # Rogue route on app2 MUST return 404
        res2_rogue = await c2.get("/api/v1/rogue_injected_probe")
        assert res2_rogue.status_code == 404

        # Health on app2 must NOT use app1's override
        res2_health = await c2.get("/api/v1/health")
        assert res2_health.json()["version"] != "compromised-v1"


# =============================================================================
# 4. Invalid Payload Submission and 422 Error Serialization
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_payload_submissions_422_serialization() -> None:
    """
    Submit invalid payloads and malformed types to domain module routes.
    Verify:
    1. HTTP status code is 422 (Unprocessable Entity).
    2. Response headers include X-Request-ID (echoed or fresh UUID) and X-Process-Time.
    3. Response JSON strictly contains keys {"code", "message", "request_id", "details"}.
    4. Code is "VALIDATION_ERROR".
    5. Details contains structured Pydantic validation error objects.
    """
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    test_cases = [
        # (Module, Method, Path, Payload, Description)
        (
            "auth",
            "POST",
            "/api/v1/auth/pair",
            {},
            "Empty object missing required pairing_code and device_name",
        ),
        (
            "auth",
            "POST",
            "/api/v1/auth/pair",
            {"pairing_code": 12345, "device_name": ["not_a_string"]},
            "Invalid type types for pairing_code and device_name",
        ),
        (
            "cards",
            "POST",
            "/api/v1/cards",
            {},
            "Empty object missing required type, urgency, title",
        ),
        (
            "cards",
            "POST",
            "/api/v1/cards",
            {"title": 12345, "type": "invalid_type", "urgency": "super_urgent"},
            "Bad types in card creation payload",
        ),
        (
            "cards",
            "PATCH",
            "/api/v1/cards/not-a-valid-uuid",
            {"status": "done"},
            "Invalid UUID in path parameter",
        ),
        (
            "runs",
            "POST",
            "/api/v1/runs",
            {},
            "Empty object missing required config_id and initial_prompt",
        ),
        (
            "runs",
            "POST",
            "/api/v1/runs",
            {"config_id": "not-a-uuid", "initial_prompt": 42},
            "Malformed UUID and bad type for initial prompt",
        ),
        (
            "skills",
            "POST",
            "/api/v1/skills/install",
            {},
            "Empty payload missing skill install fields",
        ),
    ]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for module, method, path, payload, desc in test_cases:
            client_req_id = f"test-422-{module}-{uuid4().hex[:8]}"
            headers = {
                "Authorization": "Bearer vgt_test_token",
                "X-Request-ID": client_req_id,
            }

            res = await client.request(method, path, json=payload, headers=headers)

            # Failure assertions demonstrating expected 422 behavior:
            assert res.status_code == 422, (
                f"[{desc}] Expected HTTP 422 on {method} {path}, got {res.status_code}. "
                f"Body: {res.text}"
            )

            # Headers verification
            assert res.headers.get("x-request-id") == client_req_id, (
                f"[{desc}] Failed to echo X-Request-ID header"
            )
            process_time = res.headers.get("x-process-time")
            assert process_time is not None, f"[{desc}] Missing X-Process-Time header"
            assert PROCESS_TIME_REGEX.match(process_time), (
                f"[{desc}] Invalid X-Process-Time format: {process_time}"
            )

            # Body schema verification
            body = res.json()
            assert set(body.keys()) == {"code", "message", "request_id", "details"}, (
                f"[{desc}] Unexpected schema keys: {body.keys()}"
            )
            assert body["code"] == "VALIDATION_ERROR", f"[{desc}] Code was not VALIDATION_ERROR: {body['code']}"
            assert body["request_id"] == client_req_id, f"[{desc}] Body request_id did not match client_req_id"
            assert isinstance(body["message"], str) and len(body["message"]) > 0
            assert isinstance(body["details"], list) and len(body["details"]) > 0, (
                f"[{desc}] Expected non-empty validation details list, got: {body['details']}"
            )
