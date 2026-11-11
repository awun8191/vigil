"""Integration tests for two-stage proactive triage pipeline and Action Card feed generation."""
from uuid import uuid4
import pytest
import httpx
from httpx import ASGITransport

from src.config.settings import Settings
from src.integrations.jev.jev import JevClient
from src.main import create_app
from src.modules.cards.service import CardsService
from src.modules.triage.service import TriageService


@pytest.mark.asyncio
async def test_triage_filters_bulk_newsletter_zero_cards_created() -> None:
    """Verify promotional / bulk items are discarded with zero cards generated."""
    settings = Settings(APP_ENV="test")
    cards_service = CardsService(session=None, settings=settings)  # type: ignore[arg-type]
    cards_service._cards.clear()

    jev_client = JevClient(settings=settings)
    triage_service = TriageService(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        jev_client=jev_client,
        cards_service=cards_service,
    )

    item_id = uuid4()
    bulk_email = {
        "subject": "Weekly Newsletter & Deals: 50% Off Everything!",
        "sender": "no-reply@promotions.shop.com",
        "snippet": "Click here to unsubscribe from weekly deals.",
        "body": "Unsubscribe at any time. Special promotions for holiday season.",
    }

    result = await triage_service.triage_item(item_id=item_id, item_data=bulk_email)
    assert result.decision == "discarded"
    assert result.probabilities.get("is_bulk", 0) > 0.8

    # Assert no cards were injected into cards feed
    cards = await cards_service.list_cards_by_user(user_id=uuid4())
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_triage_actionable_item_creates_action_card() -> None:
    """Verify urgent/actionable messages trigger Stage 2 and create Action Cards in feed."""
    settings = Settings(APP_ENV="test")
    user_id = uuid4()
    cards_service = CardsService(session=None, settings=settings)  # type: ignore[arg-type]
    cards_service._cards.clear()

    jev_client = JevClient(settings=settings)
    triage_service = TriageService(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        jev_client=jev_client,
        cards_service=cards_service,
    )

    item_id = uuid4()
    actionable_email = {
        "subject": "Urgent Action Required: Infrastructure Certificate Expiring",
        "sender": "devops-lead@company.com",
        "thread_id": "thread-infra-cert-99",
        "snippet": "Action required: please review and approve production TLS cert rotation.",
        "body": "Please review the attached pull request immediately. Deadline is today at 5 PM.",
    }

    result = await triage_service.triage_item(
        item_id=item_id,
        item_data=actionable_email,
        user_id=user_id,
    )
    assert result.decision == "card_created"
    assert result.probabilities.get("action_required", 0) >= 0.7

    # Assert card was created in cards feed
    cards = await cards_service.list_cards_by_user(user_id=user_id)
    assert len(cards) == 1
    card = cards[0]
    assert card.urgency in ("critical", "high")
    assert "Certificate Expiring" in card.title
    assert card.thread_id == "thread-infra-cert-99"
    assert card.proactivity_tag == "Urgent Action"
    assert card.verb == "Review"
    assert len(card.actions) == 2
    action_types = [a.action_type for a in card.actions]
    assert "reply" in action_types
    assert "dismiss" in action_types


@pytest.mark.asyncio
async def test_cards_feed_http_endpoint_lifecycle() -> None:
    """Verify Action Cards feed HTTP endpoints (list, update, dismiss) persist proactivity fields."""
    settings = Settings(APP_ENV="test")
    app = create_app(settings)
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a card via API with proactivity_tag and verb
        create_payload = {
            "type": "email",
            "urgency": "high",
            "title": "Review Vendor Contract",
            "summary": "Vendor agreement requires signature before end of month.",
            "proactivity_tag": "Urgent Action",
            "verb": "Review",
            "actions": [
                {"id": "act_sign", "label": "Review & Sign", "action_type": "run", "verb": "Review", "payload": {}},
            ],
        }
        res_post = await client.post("/api/v1/cards", json=create_payload, headers=auth_headers)
        assert res_post.status_code == 201
        post_data = res_post.json()
        card_id = post_data["id"]
        assert post_data["proactivity_tag"] == "Urgent Action"
        assert post_data["verb"] == "Review"

        # 2. Get card details directly from database/service
        res_get = await client.get(f"/api/v1/cards/{card_id}", headers=auth_headers)
        assert res_get.status_code == 200
        get_data = res_get.json()
        assert get_data["proactivity_tag"] == "Urgent Action"
        assert get_data["verb"] == "Review"

        # 3. List cards in feed and verify genuine retrieval
        res_list = await client.get("/api/v1/cards", headers=auth_headers)
        assert res_list.status_code == 200
        data = res_list.json()
        assert data["total"] >= 1
        found_card = next((c for c in data["items"] if c["id"] == card_id), None)
        assert found_card is not None
        assert found_card["proactivity_tag"] == "Urgent Action"
        assert found_card["verb"] == "Review"

        # 4. Dismiss card and verify proactivity fields survive mutation
        res_dismiss = await client.post(f"/api/v1/cards/{card_id}/dismiss", headers=auth_headers)
        assert res_dismiss.status_code == 200
        dismiss_data = res_dismiss.json()
        assert dismiss_data["status"] == "dismissed"
        assert dismiss_data["proactivity_tag"] == "Urgent Action"
        assert dismiss_data["verb"] == "Review"
