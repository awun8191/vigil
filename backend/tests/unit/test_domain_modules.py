"""
Comprehensive unit and functional tests for all 14 domain modules.

Verifies:
1. Duck-typing protocol satisfaction for all 14 domain service classes.
2. Genuine stateful behavior in domain services (CRUD operations, state transitions).
3. Pydantic v2 data schema serialization and validation errors.
4. HTTP router endpoint integration across all 14 modules using FastAPI test client.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
import httpx
from httpx import ASGITransport
import pytest
from pydantic import ValidationError

from src.config.settings import Settings
from src.main import create_app

# Domain Service imports
from src.modules.health.service import HealthService
from src.modules.health.repository import HealthRepository
from src.modules.auth.service import AuthService
from src.modules.auth.repository import AuthRepository, DeviceDTO, UserDTO, DeviceCreateDTO
from src.modules.cards.service import CardsService
from src.modules.cards.repository import CardsRepository, CardCreateDTO, CardActionDTO, CardUpdateDTO
from src.modules.triage.service import TriageService
from src.modules.triage.repository import TriageRepository, TriageResultCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.repository import (
    RunsRepository,
    RunCreateDTO,
    RunStepCreateDTO,
    MessageCreateDTO,
)
from src.modules.approvals.service import ApprovalsService
from src.modules.approvals.repository import ApprovalsRepository, ApprovalCreateDTO
from src.modules.chat.service import ChatService
from src.modules.chat.repository import (
    ChatRepository,
    ChatMessageCreateDTO,
)
from src.modules.artifacts.service import ArtifactsService
from src.modules.artifacts.repository import ArtifactsRepository, ArtifactCreateDTO
from src.modules.memory.service import MemoryService
from src.modules.memory.repository import MemoryRepository, MemoryChunkCreateDTO
from src.modules.compaction.service import CompactionService
from src.modules.compaction.repository import CompactionRepository
from src.modules.skills.service import SkillsService
from src.modules.skills.repository import SkillsRepository, SkillCreateDTO
from src.modules.connectors.service import ConnectorsService
from src.modules.connectors.repository import (
    ConnectorsRepository,
    ConnectorAccountCreateDTO,
    SyncStateDTO,
)
from src.modules.settings.service import SettingsService
from src.modules.settings.repository import SettingsRepository, AuditLogCreateDTO
from src.modules.jobs.service import JobsService
from src.modules.jobs.repository import JobsRepository, JobCreateDTO

# Wire Schemas
from src.modules.health.data import HealthStatusResponse
from src.modules.cards.data import CardCreateRequest
from src.modules.runs.data import RunCreateRequest
from src.modules.chat.data import CreateChatSessionRequest
from src.modules.memory.data import MemorySearchRequest


# =============================================================================
# 1. Protocol Duck-Typing Verification
# =============================================================================

@pytest.mark.parametrize(
    "service_cls,protocol_cls",
    [
        (HealthService, HealthRepository),
        (AuthService, AuthRepository),
        (CardsService, CardsRepository),
        (TriageService, TriageRepository),
        (RunsService, RunsRepository),
        (ApprovalsService, ApprovalsRepository),
        (ChatService, ChatRepository),
        (ArtifactsService, ArtifactsRepository),
        (MemoryService, MemoryRepository),
        (CompactionService, CompactionRepository),
        (SkillsService, SkillsRepository),
        (ConnectorsService, ConnectorsRepository),
        (SettingsService, SettingsRepository),
        (JobsService, JobsRepository),
    ],
)
def test_all_services_satisfy_repository_protocols(service_cls: type, protocol_cls: type) -> None:
    """Verify that every domain service implements all protocol methods via duck typing."""
    settings = Settings()
    service = service_cls(session=None, settings=settings)
    assert isinstance(service, protocol_cls), (
        f"{service_cls.__name__} does not satisfy runtime-checkable protocol {protocol_cls.__name__}"
    )


# =============================================================================
# 2. Stateful Service Logic Tests (All 14 Services)
# =============================================================================

@pytest.mark.asyncio
async def test_health_service_logic() -> None:
    class DummySession:
        async def execute(self, *args, **kwargs):
            class Res:
                def scalar(self):
                    return 1
            return Res()

    service = HealthService(session=DummySession(), settings=Settings())
    assert await service.check_database() is True
    status_dto = await service.get_system_status()
    assert status_dto.status == "ok"
    assert status_dto.database_connected is True
    live = await service.check_liveness()
    assert live.status == "ok"


@pytest.mark.asyncio
async def test_auth_service_logic() -> None:
    service = AuthService(session=None, settings=Settings())
    user_id = uuid4()
    now = datetime.now(timezone.utc)

    # Register device
    reg_dto = DeviceCreateDTO(
        user_id=user_id,
        name="Pixel 9 Pro",
        token_hash="hash_abc123",
        push_token="fcm_token_xyz",
    )
    device = await service.create_device(reg_dto)
    assert device.name == "Pixel 9 Pro"
    assert device.token_hash == "hash_abc123"

    # Get device by token hash
    fetched = await service.get_device_by_token_hash("hash_abc123")
    assert fetched is not None
    assert fetched.id == device.id

    # Revoke device
    revoked = await service.revoke_device(device.id)
    assert revoked is True
    post_revoke = await service.get_device_by_token_hash("hash_abc123")
    assert post_revoke is None


@pytest.mark.asyncio
async def test_cards_service_crud_lifecycle() -> None:
    service = CardsService(session=None, settings=Settings())
    user_id = uuid4()

    # Create
    dto = CardCreateDTO(
        user_id=user_id,
        type="approval",
        urgency="high",
        title="Deploy to Prod",
        summary="Action needed for deploy",
        thread_id="t-1",
        due_at=None,
        actions=[CardActionDTO(id="a1", label="Approve", action_type="primary", payload={})],
    )
    card = await service.create_card(dto)
    assert card.title == "Deploy to Prod"
    assert card.status == "new"

    # Fetch
    fetched = await service.get_card_by_id(card.id)
    assert fetched is not None
    assert fetched.id == card.id

    # List & Count
    listed = await service.list_cards_by_user(user_id=user_id)
    assert len(listed) == 1
    total = await service.count_cards(user_id=user_id)
    assert total == 1

    # Record Feedback
    updated = await service.record_feedback(card.id, "approved")
    assert updated is not None
    assert updated.feedback == "approved"


@pytest.mark.asyncio
async def test_triage_service_lifecycle() -> None:
    service = TriageService(session=None, settings=Settings())
    item_id = uuid4()
    dto = TriageResultCreateDTO(
        item_id=item_id,
        answers={"action_required": "yes"},
        probabilities={"action_required": 0.95},
        decision="card_created",
        model_version="jev-1",
    )
    result = await service.save_triage_result(dto)
    assert result.decision == "card_created"

    fetched = await service.get_triage_result_by_item(item_id)
    assert fetched is not None
    assert fetched.id == result.id

    recent = await service.list_recent_triage(limit=10)
    assert len(recent) >= 1


@pytest.mark.asyncio
async def test_runs_service_lifecycle() -> None:
    service = RunsService(session=None, settings=Settings())
    config_id = uuid4()
    run = await service.create_run(RunCreateDTO(config_id=config_id))
    assert run.status == "queued"

    # Update status
    updated = await service.update_run_status(run.id, "running")
    assert updated.status == "running"

    # Step events
    step = await service.save_run_step(
        RunStepCreateDTO(
            run_id=run.id,
            step_number=1,
            event_type="thought",
            payload={"reasoning": "analyzing request"},
        )
    )
    assert step.step_number == 1

    steps = await service.get_steps_since(run.id, 0)
    assert len(steps) == 1

    # Save and fetch messages
    msg = await service.save_message(
        MessageCreateDTO(
            role="user",
            run_id=run.id,
            content="Run task",
        )
    )
    assert msg.content == "Run task"
    messages = await service.get_messages_for_run(run.id)
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_approvals_service_lifecycle() -> None:
    service = ApprovalsService(session=None, settings=Settings())
    run_id = uuid4()
    dto = ApprovalCreateDTO(
        run_id=run_id,
        tool_call_id="call-123",
        tool_name="deploy_service",
        arguments={"cluster": "prod"},
    )
    approval = await service.create_approval(dto)
    assert approval.status == "pending"

    pending_list = await service.list_pending_approvals()
    assert len(pending_list) >= 1

    now = datetime.now(timezone.utc)
    decided = await service.update_decision(
        approval.id,
        status="approved",
        decided_at=now,
        decision_note="Looks good",
    )
    assert decided is not None
    assert decided.status == "approved"
    assert decided.decision_note == "Looks good"


@pytest.mark.asyncio
async def test_chat_service_lifecycle() -> None:
    service = ChatService(session=None, settings=Settings())
    user_id = uuid4()
    session = await service.create_session(user_id=user_id, title="Test Chat")
    assert session.title == "Test Chat"

    msg = await service.save_message(
        ChatMessageCreateDTO(
            session_id=session.session_id,
            role="user",
            content="Hello world",
        )
    )
    assert msg.content == "Hello world"

    history = await service.get_history(session.session_id)
    assert len(history) == 1
    assert history[0].content == "Hello world"

    sessions = await service.list_user_sessions(user_id)
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_artifacts_service_lifecycle() -> None:
    service = ArtifactsService(session=None, settings=Settings())
    run_id = uuid4()
    dto = ArtifactCreateDTO(
        run_id=run_id,
        kind="document",
        path="/workspace/report.pdf",
        size_bytes=1024,
        preview="Sample document",
    )
    artifact = await service.save_artifact(dto)
    assert artifact.path == "/workspace/report.pdf"

    fetched = await service.get_artifact_by_id(artifact.id)
    assert fetched is not None
    assert fetched.id == artifact.id

    by_run = await service.list_artifacts_by_run(run_id)
    assert len(by_run) == 1

    deleted = await service.delete_artifact(artifact.id)
    assert deleted is True


@pytest.mark.asyncio
async def test_memory_service_lifecycle() -> None:
    service = MemoryService(session=None, settings=Settings())
    user_id = uuid4()
    chunk_dto = MemoryChunkCreateDTO(
        user_id=user_id,
        scope="global",
        content="User likes dark theme",
        source_message_ids=[],
        embedding=[0.1] * 384,
        embedding_model="bge-small-en-v1.5",
    )
    chunk = await service.save_chunk(chunk_dto)
    assert chunk.content == "User likes dark theme"
    assert chunk.excluded is False

    # Search
    results = await service.search_decay_weighted(
        user_id=user_id,
        query_embedding=[0.1] * 1536,
        scopes=["global"],
    )
    assert len(results) >= 1

    # Exclude
    excluded = await service.set_excluded(chunk.id, True)
    assert excluded is True
    fetched = await service.get_chunk_by_id(chunk.id)
    assert fetched is not None
    assert fetched.excluded is True


@pytest.mark.asyncio
async def test_compaction_service_lifecycle() -> None:
    service = CompactionService(session=None, settings=Settings())
    run_id = uuid4()
    units = await service.load_run_history_for_compaction(run_id)
    assert isinstance(units, list)

    await service.save_compacted_history(
        run_id=run_id,
        compacted_messages=[{"role": "user", "content": "compacted"}],
        summary_block="Compacted conversation summary",
    )
    # Dry run
    dry = await service.dry_run([{"role": "user", "content": "hello"}], max_tokens=100)
    assert dry.compacted_tokens <= dry.original_tokens


@pytest.mark.asyncio
async def test_skills_service_lifecycle() -> None:
    service = SkillsService(session=None, settings=Settings())
    dto = SkillCreateDTO(
        name="web-search",
        description="Search Google and Bing",
        storage_path="/skills/web-search",
    )
    skill = await service.save_skill(dto)
    assert skill.name == "web-search"

    by_id = await service.get_by_id(skill.id)
    assert by_id is not None
    assert by_id.id == skill.id

    by_name = await service.get_by_name("web-search")
    assert by_name is not None
    assert by_name.id == skill.id

    skills_list = await service.list_skills()
    assert len(skills_list) >= 1

    updated = await service.update_status(skill.id, False)
    assert updated is not None
    assert updated.enabled is False


@pytest.mark.asyncio
async def test_connectors_service_lifecycle() -> None:
    service = ConnectorsService(session=None, settings=Settings())
    user_id = uuid4()
    dto = ConnectorAccountCreateDTO(
        user_id=user_id,
        connector_id="gmail",
        account_label="user@example.com",
        encrypted_credentials="enc_credentials_xyz",
        scopes=["https://mail.google.com/"],
    )
    account = await service.save_account(dto)
    assert account.connector_id == "gmail"

    accounts = await service.list_accounts(user_id)
    assert len(accounts) == 1

    # Sync state
    sync_state = SyncStateDTO(
        connector_account_id=account.id,
        cursor="history_12345",
        last_success_at=datetime.now(timezone.utc),
    )
    await service.save_sync_state(sync_state)
    fetched_sync = await service.get_sync_state(account.id)
    assert fetched_sync is not None
    assert fetched_sync.cursor == "history_12345"


@pytest.mark.asyncio
async def test_settings_service_lifecycle() -> None:
    service = SettingsService(session=None, settings=Settings())
    user_id = uuid4()

    # Update preferences
    updated = await service.update_user_settings(user_id, {"spending_cap_usd_monthly": 150.0})
    assert updated.spending_cap_usd_monthly == 150.0

    fetched = await service.get_user_settings(user_id)
    assert fetched is not None
    assert fetched.spending_cap_usd_monthly == 150.0

    # Audit log
    await service.record_audit_entry(
        AuditLogCreateDTO(
            actor="user",
            action="spending_cap_updated",
            subject=str(user_id),
            details={"new_cap": 150.0},
        )
    )
    audit_entries = await service.list_audit_entries()
    assert len(audit_entries) >= 1


@pytest.mark.asyncio
async def test_jobs_service_lifecycle() -> None:
    service = JobsService(session=None, settings=Settings())
    job = await service.enqueue(
        JobCreateDTO(
            type="poll_connector",
            payload={"account_id": "acc-1"},
        )
    )
    assert job.type == "poll_connector"
    assert job.status == "pending"

    claimed = await service.claim_next_job(worker_id="worker-node-1")
    assert claimed is not None
    assert claimed.status == "locked"
    assert claimed.locked_by == "worker-node-1"

    await service.mark_completed(claimed.id)
    stats = await service.get_queue_stats()
    assert stats["completed"] >= 1


# =============================================================================
# 3. Pydantic v2 Wire Schema Validation Tests
# =============================================================================

def test_wire_schemas_validation() -> None:
    """Verify Pydantic models validate required fields and reject invalid data."""
    # Health schemas
    health = HealthStatusResponse(
        status="ok",
        version="0.1.0",
        database="connected",
        timestamp=datetime.now(timezone.utc),
    )
    assert health.status == "ok"

    # CardCreateRequest valid
    req = CardCreateRequest(
        type="alert",
        urgency="medium",
        title="Check alert",
        summary="Summary of alert",
        actions=[],
    )
    assert req.type == "alert"

    # CardCreateRequest invalid (missing title)
    with pytest.raises(ValidationError):
        CardCreateRequest.model_validate({"type": "alert"})

    # RunCreateRequest
    run_req = RunCreateRequest(config_id=uuid4(), initial_prompt="run task")
    assert run_req.initial_prompt == "run task"

    # MemorySearchRequest
    mem_req = MemorySearchRequest(query="find context")
    assert mem_req.query == "find context"


# =============================================================================
# 4. HTTP Endpoints Integration Tests (All 14 Modules Mounted)
# =============================================================================

@pytest.fixture(scope="module")
def test_app():
    return create_app(Settings(DEBUG=True))


@pytest.mark.asyncio
async def test_health_http_endpoints(test_app) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/health")
        assert res.status_code == 200
        assert res.json()["status"] in ["ok", "degraded"]


@pytest.mark.asyncio
async def test_cards_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        payload = {
            "type": "decision",
            "urgency": "high",
            "title": "Review PR",
            "summary": "Review pull request #42",
            "actions": [],
        }
        res_post = await client.post("/api/v1/cards", json=payload, headers=auth_headers)
        assert res_post.status_code == 201
        data = res_post.json()
        assert data["title"] == "Review PR"
        card_id = data["id"]

        res_list = await client.get("/api/v1/cards", headers=auth_headers)
        assert res_list.status_code == 200
        assert res_list.json()["total"] >= 1

        res_get = await client.get(f"/api/v1/cards/{card_id}", headers=auth_headers)
        assert res_get.status_code == 200
        assert res_get.json()["id"] == card_id


@pytest.mark.asyncio
async def test_auth_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "device_id" in data
        assert "user_id" in data


@pytest.mark.asyncio
async def test_chat_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res_session = await client.post(
            "/api/v1/chat/sessions",
            json={"title": "General Discussion"},
            headers=auth_headers,
        )
        assert res_session.status_code == 201
        session_id = res_session.json()["session_id"]

        res_msg = await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"content": "How are you?"},
            headers=auth_headers,
        )
        assert res_msg.status_code == 200
        assert "content" in res_msg.json()


@pytest.mark.asyncio
async def test_runs_http_endpoints(test_app) -> None:
    """Drive the runs routes through one service over a journal.

    The service holds no state, so the test overrides the provider with one
    service instance. A run that one request creates is visible to the next
    request because the journal holds the row, not the service.
    """
    from src.dependencies import get_runs_service
    from src.modules.runs.repository import AgentConfigDTO

    settings = Settings(APP_ENV="test")
    service = RunsService(session=None, settings=settings)
    config = await service.save_agent_config(
        AgentConfigDTO(
            id=uuid4(),
            name="http-endpoint-agent",
            system_prompt="You answer briefly.",
            model="scripted-model",
            tools=["read_file"],
            skills=[],
            memory_scopes=["global"],
            approval_policy={},
            limits={"max_cost_micros": 1000},
        )
    )
    test_app.dependency_overrides[get_runs_service] = lambda: service

    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res_run = await client.post(
            "/api/v1/runs",
            json={"config_id": str(config.id), "initial_prompt": "analyze data"},
            headers=auth_headers,
        )
        assert res_run.status_code == 201
        run_id = res_run.json()["id"]

        res_get = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
        assert res_get.status_code == 200
        assert res_get.json()["id"] == run_id


@pytest.mark.asyncio
async def test_approvals_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/approvals", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_artifacts_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    run_id = uuid4()
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get(f"/api/v1/artifacts/run/{run_id}", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_skills_http_endpoints(test_app) -> None:
    # The skills router is the only endpoint test that reaches PostgreSQL.
    # The process-wide engine singleton binds to the first event loop that
    # borrows it, so a shared module app reuses a stale loop-bound pool here.
    # A test-local engine keeps this test hermetic without changing its asserts.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.dependencies import get_db_session

    engine = create_async_engine(Settings(DEBUG=True).DATABASE_URL)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _test_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    test_app.dependency_overrides[get_db_session] = _test_session
    try:
        auth_headers = {"Authorization": "Bearer vgt_testtoken"}
        async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            res = await client.get("/api/v1/skills", headers=auth_headers)
            assert res.status_code == 200
            assert isinstance(res.json(), list)
    finally:
        test_app.dependency_overrides.pop(get_db_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_connectors_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/connectors/accounts", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_settings_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/settings", headers=auth_headers)
        assert res.status_code == 200
        assert "spending_cap_usd_monthly" in res.json()

        res_limits = await client.get("/api/v1/settings/limits", headers=auth_headers)
        assert res_limits.status_code == 200
        assert "max_steps_per_run" in res_limits.json()


@pytest.mark.asyncio
async def test_jobs_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/jobs", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "pending" in data
        assert "locked" in data


@pytest.mark.asyncio
async def test_triage_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.get("/api/v1/triage/recent", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_compaction_http_endpoints(test_app) -> None:
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}
    async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/compaction/dry-run",
            json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert "compacted_tokens" in res.json()
