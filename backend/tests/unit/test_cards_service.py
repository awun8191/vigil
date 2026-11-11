"""Unit tests for cards service, wire schemas, triage synthesis, and artifact extensions."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
import pytest
from pydantic import ValidationError

from src.config.settings import Settings
from src.modules.artifacts.data import ArtifactCreateRequest
from src.modules.cards.data import CardActionSchema, CardCreateRequest, CardResponse
from src.modules.cards.repository import (
    CardActionDTO,
    CardCreateDTO,
    CardDTO,
    CardUpdateDTO,
)
from src.modules.cards.service import CardsService
from src.modules.triage.service import TriageService
from src.schemas.triage import CardActionModel, CardModel


def test_card_action_schema_supports_verb() -> None:
    """Verify CardActionSchema serializes and validates with optional verb field."""
    action = CardActionSchema(
        id="act_reply",
        label="Quick Reply",
        action_type="reply",
        verb="Reply",
        payload={"thread_id": "thread-123"},
    )
    assert action.id == "act_reply"
    assert action.label == "Quick Reply"
    assert action.verb == "Reply"
    assert action.action_type == "reply"

    dump = action.model_dump()
    assert dump["verb"] == "Reply"

    # Also works without verb
    action_no_verb = CardActionSchema(
        id="act_dismiss",
        label="Dismiss",
        action_type="dismiss",
    )
    assert action_no_verb.verb is None


def test_card_response_and_create_request_support_proactivity_tag_and_verb() -> None:
    """Verify CardResponse and CardCreateRequest serialize proactivity_tag and verb."""
    req = CardCreateRequest(
        type="email",
        urgency="critical",
        title="Server Outage Alert",
        summary="API Gateway is reporting 502 errors.",
        proactivity_tag="Urgent Action",
        verb="Resolve",
        actions=[
            CardActionSchema(
                id="act_resolve",
                label="Resolve Issue",
                action_type="run",
                verb="Resolve",
            )
        ],
    )
    assert req.proactivity_tag == "Urgent Action"
    assert req.verb == "Resolve"
    assert len(req.actions) == 1
    assert req.actions[0].verb == "Resolve"

    now = datetime.now(timezone.utc)
    card_id = uuid4()
    user_id = uuid4()
    resp = CardResponse(
        id=card_id,
        user_id=user_id,
        type="email",
        urgency="critical",
        proactivity_tag="Urgent Action",
        verb="Resolve",
        title="Server Outage Alert",
        summary="API Gateway is reporting 502 errors.",
        status="new",
        actions=[
            CardActionSchema(
                id="act_resolve",
                label="Resolve Issue",
                action_type="run",
                verb="Resolve",
            )
        ],
        created_at=now,
        updated_at=now,
    )
    assert resp.proactivity_tag == "Urgent Action"
    assert resp.verb == "Resolve"
    assert resp.actions[0].verb == "Resolve"


def test_card_repository_dtos_proactivity_tag_and_verb() -> None:
    """Verify CardActionDTO, CardDTO, CardCreateDTO, and CardUpdateDTO have proactivity_tag and verb."""
    now = datetime.now(timezone.utc)
    card_id = uuid4()
    user_id = uuid4()

    action_dto = CardActionDTO(
        id="act_1",
        label="Approve Changes",
        action_type="run",
        verb="Approve",
    )
    assert action_dto.verb == "Approve"

    create_dto = CardCreateDTO(
        user_id=user_id,
        type="email",
        urgency="high",
        title="Contract Renewal",
        proactivity_tag="Decision Needed",
        verb="Review",
        actions=[action_dto],
    )
    assert create_dto.proactivity_tag == "Decision Needed"
    assert create_dto.verb == "Review"
    assert create_dto.actions[0].verb == "Approve"

    card_dto = CardDTO(
        id=card_id,
        user_id=user_id,
        item_id=None,
        thread_id="t-1",
        type="email",
        urgency="high",
        title="Contract Renewal",
        summary="Vendor contract expires next week.",
        due_at=None,
        status="new",
        actions=[action_dto],
        feedback=None,
        created_at=now,
        updated_at=now,
        proactivity_tag="Decision Needed",
        verb="Review",
    )
    assert card_dto.proactivity_tag == "Decision Needed"
    assert card_dto.verb == "Review"

    update_dto = CardUpdateDTO(
        proactivity_tag="Autonomous Observation",
        verb="Monitor",
    )
    assert update_dto.proactivity_tag == "Autonomous Observation"
    assert update_dto.verb == "Monitor"


def test_sqlalchemy_card_models_have_proactivity_tag_and_verb_columns() -> None:
    """Verify SQLAlchemy ORM models include proactivity_tag and verb mapped columns."""
    assert hasattr(CardModel, "proactivity_tag")
    assert hasattr(CardModel, "verb")
    assert hasattr(CardActionModel, "verb")

    # Column attributes check
    proactivity_col = CardModel.__table__.columns.get("proactivity_tag")
    card_verb_col = CardModel.__table__.columns.get("verb")
    action_verb_col = CardActionModel.__table__.columns.get("verb")

    assert proactivity_col is not None
    assert card_verb_col is not None
    assert action_verb_col is not None


@pytest.mark.asyncio
async def test_triage_synthesizes_proactivity_tags_and_active_verbs() -> None:
    """Verify TriageService generates cards with proactivity tags and active verbs."""
    settings = Settings(APP_ENV="test")
    user_id = uuid4()
    cards_service = CardsService(session=None, settings=settings)  # type: ignore[arg-type]
    cards_service._cards.clear()

    triage_service = TriageService(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        jev_client=None,
        cards_service=cards_service,
    )

    item_id = uuid4()
    item_data = {
        "subject": "Urgent Security Vulnerability Patch",
        "snippet": "Critical CVE detected in auth dependency.",
        "thread_id": "thread-sec-cve",
    }

    result = await triage_service.triage_item(
        item_id=item_id,
        item_data=item_data,
        user_id=user_id,
    )

    assert result.decision == "card_created"
    cards = await cards_service.list_cards_by_user(user_id=user_id)
    assert len(cards) == 1
    card = cards[0]
    # Check proactivity tag and active verb on card
    assert card.proactivity_tag == "Urgent Action"
    assert card.verb is not None
    assert card.verb == "Review"
    # Check that actions have verbs
    assert len(card.actions) == 2
    action_verbs = {a.id: a.verb for a in card.actions}
    assert action_verbs["quick_reply"] == "Reply"
    assert action_verbs["mark_done"] == "Dismiss"


@pytest.mark.asyncio
async def test_cards_service_preserves_proactivity_tag_and_verb() -> None:
    """Verify CardsService persists and updates proactivity_tag and verb."""
    settings = Settings(APP_ENV="test")
    cards_service = CardsService(session=None, settings=settings)  # type: ignore[arg-type]
    cards_service._cards.clear()

    user_id = uuid4()
    dto = CardCreateDTO(
        user_id=user_id,
        type="email",
        urgency="high",
        title="Infrastructure Alert",
        summary="Database failover completed.",
        proactivity_tag="Urgent Action",
        verb="Review",
    )
    created = await cards_service.create_card(dto)
    assert created.proactivity_tag == "Urgent Action"
    assert created.verb == "Review"

    # Update without changing tags retains existing
    updated = await cards_service.update_card(created.id, CardUpdateDTO(summary="Updated summary"))
    assert updated is not None
    assert updated.summary == "Updated summary"
    assert updated.proactivity_tag == "Urgent Action"
    assert updated.verb == "Review"

    # Update with new proactivity tag and verb
    updated_again = await cards_service.update_card(
        created.id,
        CardUpdateDTO(proactivity_tag="Decision Needed", verb="Approve"),
    )
    assert updated_again is not None
    assert updated_again.proactivity_tag == "Decision Needed"
    assert updated_again.verb == "Approve"


def test_artifact_create_request_accepts_micro_app_and_screenshot() -> None:
    """Verify ArtifactCreateRequest accepts micro_app and screenshot kinds."""
    run_id = uuid4()

    micro_app = ArtifactCreateRequest(
        run_id=run_id,
        kind="micro_app",
        path="app/index.html",
        size_bytes=1024,
        preview="<!doctype html><html></html>",
    )
    assert micro_app.kind == "micro_app"

    screenshot = ArtifactCreateRequest(
        run_id=run_id,
        kind="screenshot",
        path="screenshots/capture.png",
        size_bytes=4096,
        preview=None,
    )
    assert screenshot.kind == "screenshot"

    with pytest.raises(ValidationError):
        ArtifactCreateRequest(
            run_id=run_id,
            kind="unsupported_kind",  # type: ignore[arg-type]
            path="file.txt",
            size_bytes=10,
        )
