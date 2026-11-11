"""Adversarial stress and empirical validation suite for Vigil Milestone 1 schemas.

Empirically challenges:
1. Unique Constraints:
   - uq_connected_accounts_user_conn_label (duplicate rejection, tenant isolation, concurrent contention)
   - uq_connector_items_account_external_id (duplicate rejection, account isolation, concurrent contention)
   - devices.token_hash (global uniqueness enforcement)
   - triage_decisions uniqueness per (item_id, model_version), re-triage allowed
2. Referential Integrity & Cascades:
   - Rejection of invalid/orphan foreign keys
   - CASCADE on user deletion across full deep hierarchy
   - SET NULL on connector item deletion (preserving cards)
   - SET NULL on card deletion (preserving runs and execution audit history)
   - SET NULL on parent run deletion (preserving child runs)
3. Vector Properties & HNSW Index:
   - Vector(384) dimension mismatch rejection (0, 1, 512, 383, 385, 3072)
   - Vector exact 384-dim acceptance and cosine distance calculations
   - PostgreSQL catalog inspection of HNSW index (hnsw access method, vector_cosine_ops, m=16, ef_construction=64)
   - Query plan EXPLAIN verification for Index Scan on ix_memory_chunks_embedding_hnsw
   - Session-level hnsw.ef_search tuning
4. Concurrent Stress Harness:
   - Parallel race condition insertion stress
   - Concurrent memory chunk batch inserts and similarity queries
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings
from src.schemas import (
    ApprovalModel,
    ArtifactModel,
    CardActionModel,
    CardModel,
    ConnectedAccountModel,
    ConnectorItemModel,
    ConnectorSyncStateModel,
    ConnectorToolModel,
    DeviceModel,
    IdempotencyRecordModel,
    JobModel,
    MemoryChunkModel,
    MessageModel,
    RunModel,
    ThreadModel,
    TriageDecisionModel,
    UserModel,
)


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    backend_dir = Path(__file__).resolve().parent.parent.parent
    ini_path = backend_dir / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


@pytest_asyncio.fixture
async def db_engine(alembic_cfg: Config) -> AsyncGenerator[AsyncEngine, None]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, pool_size=20, max_overflow=10)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1;"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    # Ensure schema is upgraded to head
    command.upgrade(alembic_cfg, "head")

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine) -> sessionmaker:
    return sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


# ==============================================================================
# 1. UNIQUE CONSTRAINTS ADVERSARIAL CHALLENGES
# ==============================================================================


@pytest.mark.asyncio
class TestUniqueConstraintsAdversarial:
    """Stress-test unique constraints against duplicates, multi-tenant isolation, and race conditions."""

    async def test_connected_accounts_unique_constraint_enforcement(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify uq_connected_accounts_user_conn_label prevents duplicate (user, conn, label)
        while permitting same label across different users or different connectors.
        """
        async with session_factory() as session:
            u1_id = uuid.uuid4()
            u2_id = uuid.uuid4()
            session.add_all([
                UserModel(id=u1_id, display_name="User 1"),
                UserModel(id=u2_id, display_name="User 2"),
            ])
            await session.commit()

            try:
                # 1. First account insert succeeds
                acc1 = ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=u1_id,
                    connector_id="gmail",
                    account_label="work",
                    encrypted_credentials="enc_1",
                )
                session.add(acc1)
                await session.commit()

                # 2. Duplicate insert for same user, same connector, same label MUST fail
                acc_dup = ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=u1_id,
                    connector_id="gmail",
                    account_label="work",
                    encrypted_credentials="enc_dup",
                )
                session.add(acc_dup)
                with pytest.raises(IntegrityError) as exc_info:
                    await session.commit()
                assert "uq_connected_accounts_user_conn_label" in str(exc_info.value)
                await session.rollback()

                # 3. Multi-tenancy check: user 2 can use same connector and label
                acc_u2 = ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=u2_id,
                    connector_id="gmail",
                    account_label="work",
                    encrypted_credentials="enc_u2",
                )
                session.add(acc_u2)
                await session.commit()

                # 4. Same user can link another account with different label
                acc_personal = ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=u1_id,
                    connector_id="gmail",
                    account_label="personal",
                    encrypted_credentials="enc_personal",
                )
                session.add(acc_personal)
                await session.commit()

                # 5. Same user can link different connector with same label
                acc_outlook = ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=u1_id,
                    connector_id="outlook",
                    account_label="work",
                    encrypted_credentials="enc_outlook",
                )
                session.add(acc_outlook)
                await session.commit()

            finally:
                # Cleanup
                await session.execute(
                    sa.text("DELETE FROM users WHERE id IN (:u1, :u2);"),
                    {"u1": u1_id, "u2": u2_id},
                )
                await session.commit()

    async def test_connected_accounts_concurrent_race_condition(
        self, session_factory: sessionmaker
    ) -> None:
        """Adversarial race condition: 10 concurrent tasks attempt to insert identical (user, conn, label).
        Exactly 1 must succeed and 9 must fail cleanly with IntegrityError.
        """
        async with session_factory() as setup_session:
            user_id = uuid.uuid4()
            setup_session.add(UserModel(id=user_id, display_name="Race User"))
            await setup_session.commit()

        successes = 0
        conflicts = 0

        async def worker_insert(worker_idx: int) -> bool:
            async with session_factory() as s:
                try:
                    acc = ConnectedAccountModel(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        connector_id="gmail",
                        account_label="concurrent_label",
                        encrypted_credentials=f"enc_{worker_idx}",
                    )
                    s.add(acc)
                    await s.commit()
                    return True
                except IntegrityError:
                    await s.rollback()
                    return False

        results = await asyncio.gather(*[worker_insert(i) for i in range(10)])
        successes = sum(1 for r in results if r is True)
        conflicts = sum(1 for r in results if r is False)

        try:
            assert successes == 1, f"Expected exactly 1 success, got {successes}"
            assert conflicts == 9, f"Expected 9 conflicts, got {conflicts}"
        finally:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await cleanup_session.commit()

    async def test_connector_items_unique_constraint_enforcement(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify uq_connector_items_account_external_id prevents duplicate (account, external_id)
        while allowing same external_id across different accounts.
        """
        async with session_factory() as session:
            user_id = uuid.uuid4()
            acc1_id = uuid.uuid4()
            acc2_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="Item User"))
            await session.flush()
            session.add_all([
                ConnectedAccountModel(
                    id=acc1_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="acc1",
                    encrypted_credentials="enc",
                ),
                ConnectedAccountModel(
                    id=acc2_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="acc2",
                    encrypted_credentials="enc",
                ),
            ])
            await session.commit()

            try:
                # 1. Insert item into acc1 succeeds
                now = datetime.now(timezone.utc)
                item1 = ConnectorItemModel(
                    id=uuid.uuid4(),
                    connector_account_id=acc1_id,
                    external_id="msg_001",
                    thread_id="t_1",
                    sender="alice@test.com",
                    subject="Sub 1",
                    body_text="Body 1",
                    received_at=now,
                )
                session.add(item1)
                await session.commit()

                # 2. Duplicate item for same account and external_id MUST fail
                item_dup = ConnectorItemModel(
                    id=uuid.uuid4(),
                    connector_account_id=acc1_id,
                    external_id="msg_001",
                    thread_id="t_1_dup",
                    sender="alice@test.com",
                    subject="Sub Dup",
                    body_text="Body Dup",
                    received_at=now,
                )
                session.add(item_dup)
                with pytest.raises(IntegrityError) as exc_info:
                    await session.commit()
                assert "uq_connector_items_account_external_id" in str(exc_info.value)
                await session.rollback()

                # 3. Same external_id in different account acc2 MUST succeed
                item_acc2 = ConnectorItemModel(
                    id=uuid.uuid4(),
                    connector_account_id=acc2_id,
                    external_id="msg_001",
                    thread_id="t_2",
                    sender="alice@test.com",
                    subject="Sub Acc2",
                    body_text="Body Acc2",
                    received_at=now,
                )
                session.add(item_acc2)
                await session.commit()

                # 4. Same account acc1 with different external_id MUST succeed
                item_msg2 = ConnectorItemModel(
                    id=uuid.uuid4(),
                    connector_account_id=acc1_id,
                    external_id="msg_002",
                    thread_id="t_1",
                    sender="alice@test.com",
                    subject="Sub 2",
                    body_text="Body 2",
                    received_at=now,
                )
                session.add(item_msg2)
                await session.commit()

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()

    async def test_connector_items_concurrent_race_condition(
        self, session_factory: sessionmaker
    ) -> None:
        """Adversarial race condition: 10 concurrent tasks attempt to insert identical (account, external_id).
        Exactly 1 must succeed and 9 must fail cleanly.
        """
        async with session_factory() as setup_session:
            user_id = uuid.uuid4()
            acc_id = uuid.uuid4()
            setup_session.add(UserModel(id=user_id, display_name="Race Item User"))
            await setup_session.flush()
            setup_session.add(
                ConnectedAccountModel(
                    id=acc_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="race_acc",
                    encrypted_credentials="enc",
                )
            )
            await setup_session.commit()

        async def worker_insert_item(worker_idx: int) -> bool:
            async with session_factory() as s:
                try:
                    item = ConnectorItemModel(
                        id=uuid.uuid4(),
                        connector_account_id=acc_id,
                        external_id="race_msg_100",
                        thread_id="th_race",
                        sender="bot@test.com",
                        subject=f"Race {worker_idx}",
                        body_text="Content",
                        received_at=datetime.now(timezone.utc),
                    )
                    s.add(item)
                    await s.commit()
                    return True
                except IntegrityError:
                    await s.rollback()
                    return False

        results = await asyncio.gather(*[worker_insert_item(i) for i in range(10)])
        successes = sum(1 for r in results if r is True)
        conflicts = sum(1 for r in results if r is False)

        try:
            assert successes == 1, f"Expected 1 item insert success, got {successes}"
            assert conflicts == 9, f"Expected 9 item conflicts, got {conflicts}"
        finally:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await cleanup_session.commit()

    async def test_device_token_hash_globally_unique(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify devices.token_hash enforces global uniqueness across devices and users."""
        async with session_factory() as session:
            u1_id = uuid.uuid4()
            u2_id = uuid.uuid4()
            session.add_all([
                UserModel(id=u1_id, display_name="Dev User 1"),
                UserModel(id=u2_id, display_name="Dev User 2"),
            ])
            await session.commit()

            try:
                dev1 = DeviceModel(
                    id=uuid.uuid4(),
                    user_id=u1_id,
                    name="Phone 1",
                    token_hash="shared_hash_abc_123_456_789_012_345",
                )
                session.add(dev1)
                await session.commit()

                # Attempting to pair another device (even on different user) with same token hash
                dev2 = DeviceModel(
                    id=uuid.uuid4(),
                    user_id=u2_id,
                    name="Phone 2",
                    token_hash="shared_hash_abc_123_456_789_012_345",
                )
                session.add(dev2)
                with pytest.raises(IntegrityError) as exc_info:
                    await session.commit()
                assert "devices_token_hash" in str(exc_info.value).lower()
                await session.rollback()

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id IN (:u1, :u2);"),
                    {"u1": u1_id, "u2": u2_id},
                )
                await session.commit()

    async def test_triage_decision_item_id_unique(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify triage_decisions is unique per item and model version, and a re-triage is legal."""
        async with session_factory() as session:
            user_id = uuid.uuid4()
            acc_id = uuid.uuid4()
            item_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="Triage Unique User"))
            await session.flush()
            session.add(
                ConnectedAccountModel(
                    id=acc_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="triage_acc",
                    encrypted_credentials="enc",
                )
            )
            await session.flush()
            session.add(
                ConnectorItemModel(
                    id=item_id,
                    connector_account_id=acc_id,
                    external_id="triage_ext_1",
                    thread_id="th_1",
                    sender="sender@test.com",
                    subject="Triage Sub",
                    body_text="Body",
                    received_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

            try:
                dec1 = TriageDecisionModel(
                    id=uuid.uuid4(),
                    item_id=item_id,
                    decision="TRIAGE",
                    model_version="jev-v1",
                )
                session.add(dec1)
                await session.commit()

                dec2 = TriageDecisionModel(
                    id=uuid.uuid4(),
                    item_id=item_id,
                    decision="IGNORE",
                    model_version="jev-v1",
                )
                session.add(dec2)
                with pytest.raises(IntegrityError) as exc_info:
                    await session.commit()
                assert "triage_decisions" in str(exc_info.value).lower()
                await session.rollback()

                # A re-triage stores a second verdict for a new model version.
                retriage = TriageDecisionModel(
                    id=uuid.uuid4(),
                    item_id=item_id,
                    decision="TRIAGE",
                    model_version="jev-v2",
                )
                session.add(retriage)
                await session.commit()

                count_res = await session.execute(
                    sa.text(
                        "SELECT count(*) FROM triage_decisions WHERE item_id = :iid;"
                    ),
                    {"iid": item_id},
                )
                assert count_res.scalar() == 2

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()


# ==============================================================================
# 2. REFERENTIAL INTEGRITY & CASCADES ADVERSARIAL CHALLENGES
# ==============================================================================


@pytest.mark.asyncio
class TestReferentialIntegrityAdversarial:
    """Stress-test foreign key validation, cascading deletions, and SET NULL semantics."""

    async def test_orphan_foreign_keys_rejected(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify that inserting rows with non-existent foreign keys is strictly rejected across tables."""
        fake_id = uuid.uuid4()

        async with session_factory() as session:
            # 1. Device with fake user_id
            session.add(
                DeviceModel(id=uuid.uuid4(), user_id=fake_id, name="Orphan", token_hash="f_hash")
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            # 2. ConnectedAccount with fake user_id
            session.add(
                ConnectedAccountModel(
                    id=uuid.uuid4(),
                    user_id=fake_id,
                    connector_id="gmail",
                    account_label="orphan",
                    encrypted_credentials="enc",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            # 3. ConnectorItem with fake connector_account_id
            session.add(
                ConnectorItemModel(
                    id=uuid.uuid4(),
                    connector_account_id=fake_id,
                    external_id="ext_fake",
                    thread_id="th_fake",
                    sender="snd",
                    subject="sub",
                    body_text="body",
                    received_at=datetime.now(timezone.utc),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            # 4. Card with fake user_id
            session.add(
                CardModel(
                    id=uuid.uuid4(),
                    user_id=fake_id,
                    type="email",
                    urgency="high",
                    title="Orphan Card",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            # 5. MemoryChunk with fake user_id
            session.add(
                MemoryChunkModel(
                    id=uuid.uuid4(),
                    user_id=fake_id,
                    scope="user",
                    content="Orphan chunk",
                    embedding=[0.0] * 384,
                    embedding_model="text-embedding-3-small",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    async def test_cascade_delete_user_deep_hierarchy(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify deleting a User cascades across all connected levels:
        User -> Device, ConnectedAccount (-> SyncState, Item -> TriageDecision),
                Card (-> CardActions), Thread (-> Thread Messages), MemoryChunks.
        Also verifies that Run referencing Card has card_id SET NULL (preserving run audit history).
        """
        async with session_factory() as session:
            # 1. Build deep tree
            user_id = uuid.uuid4()
            dev_id = uuid.uuid4()
            acc_id = uuid.uuid4()
            sync_id = acc_id
            item_id = uuid.uuid4()
            triage_id = uuid.uuid4()
            card_id = uuid.uuid4()
            action_id = uuid.uuid4()
            thread_id = uuid.uuid4()
            msg_id = uuid.uuid4()
            mem_id = uuid.uuid4()
            run_id = uuid.uuid4()
            approval_id = uuid.uuid4()
            artifact_id = uuid.uuid4()

            now = datetime.now(timezone.utc)

            # User
            session.add(UserModel(id=user_id, display_name="Deep Tree User"))
            await session.flush()

            # Device, ConnectedAccount, Thread, MemoryChunk
            session.add_all([
                DeviceModel(id=dev_id, user_id=user_id, name="Dev", token_hash="dev_hash_tree"),
                ConnectedAccountModel(
                    id=acc_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="tree_acc",
                    encrypted_credentials="enc",
                ),
                ThreadModel(id=thread_id, user_id=user_id, title="Tree Thread"),
                MemoryChunkModel(
                    id=mem_id,
                    user_id=user_id,
                    scope="user",
                    content="Tree memory",
                    embedding=[0.0] * 384,
                    embedding_model="test-model",
                ),
            ])
            await session.flush()

            # SyncState, Item
            session.add_all([
                ConnectorSyncStateModel(connector_account_id=acc_id, cursor="cur_001"),
                ConnectorItemModel(
                    id=item_id,
                    connector_account_id=acc_id,
                    external_id="ext_tree",
                    thread_id="th_tree",
                    sender="snd",
                    subject="sub",
                    body_text="body",
                    received_at=now,
                ),
            ])
            await session.flush()

            # TriageDecision, Card
            session.add_all([
                TriageDecisionModel(
                    id=triage_id,
                    item_id=item_id,
                    decision="TRIAGE",
                    model_version="jev-1",
                ),
                CardModel(
                    id=card_id,
                    user_id=user_id,
                    item_id=item_id,
                    type="email",
                    urgency="critical",
                    title="Tree Card",
                ),
            ])
            await session.flush()

            # CardAction, Run (linked to card), Thread Message
            session.add_all([
                CardActionModel(
                    id=action_id,
                    card_id=card_id,
                    action_key="act_1",
                    label="Reply",
                    action_type="reply",
                ),
                RunModel(
                    id=run_id,
                    card_id=card_id,
                    status="running",
                ),
                MessageModel(
                    id=msg_id,
                    session_id=thread_id,
                    role="user",
                    content="Hello Tree",
                ),
            ])
            await session.flush()

            # Approval, Artifact on Run
            session.add_all([
                ApprovalModel(
                    id=approval_id,
                    run_id=run_id,
                    tool_call_id="tc_1",
                    tool_name="send_email",
                    arguments={"to": "test@test.com"},
                ),
                ArtifactModel(
                    id=artifact_id,
                    run_id=run_id,
                    kind="file",
                    path="/tmp/test",
                    size_bytes=100,
                ),
            ])
            await session.commit()

            # 2. Verify all records exist before deletion
            assert (await session.get(UserModel, user_id)) is not None
            assert (await session.get(DeviceModel, dev_id)) is not None
            assert (await session.get(ConnectedAccountModel, acc_id)) is not None
            assert (await session.get(ConnectorSyncStateModel, sync_id)) is not None
            assert (await session.get(ConnectorItemModel, item_id)) is not None
            assert (await session.get(TriageDecisionModel, triage_id)) is not None
            assert (await session.get(CardModel, card_id)) is not None
            assert (await session.get(CardActionModel, action_id)) is not None
            assert (await session.get(ThreadModel, thread_id)) is not None
            assert (await session.get(MessageModel, msg_id)) is not None
            assert (await session.get(MemoryChunkModel, mem_id)) is not None
            assert (await session.get(RunModel, run_id)) is not None

            # 3. DELETE User -> Trigger Cascades
            await session.execute(
                sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
            )
            await session.commit()

            # 4. Verify cascade deletions
            assert (await session.get(UserModel, user_id)) is None
            assert (await session.get(DeviceModel, dev_id)) is None
            assert (await session.get(ConnectedAccountModel, acc_id)) is None
            assert (await session.get(ConnectorSyncStateModel, sync_id)) is None
            assert (await session.get(ConnectorItemModel, item_id)) is None
            assert (await session.get(TriageDecisionModel, triage_id)) is None
            assert (await session.get(CardModel, card_id)) is None
            assert (await session.get(CardActionModel, action_id)) is None
            assert (await session.get(ThreadModel, thread_id)) is None
            assert (await session.get(MessageModel, msg_id)) is None
            assert (await session.get(MemoryChunkModel, mem_id)) is None

            # 5. Verify Run is preserved with card_id SET NULL
            preserved_run = await session.get(RunModel, run_id)
            assert preserved_run is not None, "Run should be preserved when card is deleted"
            assert preserved_run.card_id is None, "Run.card_id should be SET NULL"

            # 6. Verify Approvals and Artifacts for preserved Run are also preserved
            assert (await session.get(ApprovalModel, approval_id)) is not None
            assert (await session.get(ArtifactModel, artifact_id)) is not None

            # Cleanup preserved run (which cascades to approvals/artifacts)
            await session.execute(
                sa.text("DELETE FROM runs WHERE id = :rid;"), {"rid": run_id}
            )
            await session.commit()
            assert (await session.get(ApprovalModel, approval_id)) is None
            assert (await session.get(ArtifactModel, artifact_id)) is None

    async def test_set_null_on_connector_item_deletion(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify that deleting a connector_item sets card.item_id to NULL without deleting the card."""
        async with session_factory() as session:
            user_id = uuid.uuid4()
            acc_id = uuid.uuid4()
            item_id = uuid.uuid4()
            card_id = uuid.uuid4()
            triage_id = uuid.uuid4()

            session.add(UserModel(id=user_id, display_name="SetNull User"))
            await session.flush()
            session.add(
                ConnectedAccountModel(
                    id=acc_id,
                    user_id=user_id,
                    connector_id="gmail",
                    account_label="sn_acc",
                    encrypted_credentials="enc",
                )
            )
            await session.flush()
            session.add(
                ConnectorItemModel(
                    id=item_id,
                    connector_account_id=acc_id,
                    external_id="sn_ext_1",
                    thread_id="th_sn",
                    sender="snd",
                    subject="sub",
                    body_text="body",
                    received_at=datetime.now(timezone.utc),
                )
            )
            await session.flush()
            session.add_all([
                TriageDecisionModel(
                    id=triage_id,
                    item_id=item_id,
                    decision="TRIAGE",
                    model_version="jev-1",
                ),
                CardModel(
                    id=card_id,
                    user_id=user_id,
                    item_id=item_id,
                    type="email",
                    urgency="high",
                    title="Persisting Card",
                ),
            ])
            await session.commit()

            try:
                # Delete connector item
                await session.execute(
                    sa.text("DELETE FROM connector_items WHERE id = :iid;"), {"iid": item_id}
                )
                await session.commit()

                # Triage decision MUST be cascade deleted
                assert (await session.get(TriageDecisionModel, triage_id)) is None

                # Card MUST still exist with item_id SET NULL
                card = await session.get(CardModel, card_id)
                assert card is not None
                assert card.item_id is None
                assert card.title == "Persisting Card"

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()

    async def test_set_null_on_parent_run_deletion(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify self-referential runs hierarchy: deleting parent run sets child_run.parent_run_id to NULL."""
        async with session_factory() as session:
            parent_id = uuid.uuid4()
            child_id = uuid.uuid4()

            session.add(RunModel(id=parent_id, depth=0, status="completed"))
            await session.flush()
            session.add(RunModel(id=child_id, parent_run_id=parent_id, depth=1, status="completed"))
            await session.commit()

            try:
                # Delete parent run
                await session.execute(
                    sa.text("DELETE FROM runs WHERE id = :pid;"), {"pid": parent_id}
                )
                await session.commit()

                # Child run should survive with parent_run_id = NULL
                child = await session.get(RunModel, child_id)
                assert child is not None
                assert child.parent_run_id is None

            finally:
                await session.execute(
                    sa.text("DELETE FROM runs WHERE id = :cid;"), {"cid": child_id}
                )
                await session.commit()


# ==============================================================================
# 3. VECTOR PROPERTIES & HNSW INDEX ADVERSARIAL CHALLENGES
# ==============================================================================


@pytest.mark.asyncio
class TestVectorPropertiesAdversarial:
    """Stress-test pgvector Vector(384) dimensional enforcement, operators, and HNSW index."""

    @pytest.mark.parametrize(
        "bad_dim",
        [
            0,      # empty vector
            1,      # single dimension
            512,    # common embedding size, incompatible with 384
            383,    # off-by-one under
            385,    # off-by-one over
            3072,   # large embedding size (e.g. text-embedding-3-large)
        ],
    )
    async def test_vector_dimension_mismatch_rejected(
        self, session_factory: sessionmaker, bad_dim: int
    ) -> None:
        """PostgreSQL pgvector MUST reject incompatible vector dimensions with a DataError / DBAPIError."""
        async with session_factory() as session:
            user_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="Vec Error User"))
            await session.commit()

            try:
                vec_str = "[" + ",".join(["0.01"] * bad_dim) + "]"
                chunk_id = uuid.uuid4()

                with pytest.raises(DBAPIError) as exc_info:
                    await session.execute(
                        sa.text(
                            "INSERT INTO memory_chunks (id, user_id, scope, content, embedding, embedding_model) "
                            "VALUES (:id, :uid, 'user', 'bad dim', CAST(:emb AS vector), 'test');"
                        ),
                        {"id": chunk_id, "uid": user_id, "emb": vec_str},
                    )
                    await session.commit()

                # Error message from PostgreSQL pgvector
                await session.rollback()
                err_text = str(exc_info.value).lower()
                assert (
                    "expected 384 dimensions" in err_text
                    or "at least 1 dimension" in err_text
                    or "different size" in err_text
                )
            finally:
                await session.rollback()
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()

    async def test_vector_exact_384_dimensions_accepted(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify inserting exact 384 dimensions succeeds for various value patterns (zeros, negative/positive floats)."""
        async with session_factory() as session:
            user_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="Valid Vec User"))
            await session.commit()

            try:
                # 1. Standard float vector
                c1_id = uuid.uuid4()
                c1 = MemoryChunkModel(
                    id=c1_id,
                    user_id=user_id,
                    scope="user",
                    content="Chunk 1",
                    embedding=[(i % 100) * 0.01 - 0.5 for i in range(384)],
                    embedding_model="text-embedding-3-small",
                )
                session.add(c1)

                # 2. All zeros vector
                c2_id = uuid.uuid4()
                c2 = MemoryChunkModel(
                    id=c2_id,
                    user_id=user_id,
                    scope="user",
                    content="Zeros Chunk",
                    embedding=[0.0] * 384,
                    embedding_model="text-embedding-3-small",
                )
                session.add(c2)
                await session.commit()

                retrieved_1 = await session.get(MemoryChunkModel, c1_id)
                assert retrieved_1 is not None
                assert len(retrieved_1.embedding) == 384
                assert abs(retrieved_1.embedding[0] - (-0.5)) < 1e-5

                retrieved_2 = await session.get(MemoryChunkModel, c2_id)
                assert retrieved_2 is not None
                assert len(retrieved_2.embedding) == 384
                assert all(v == 0.0 for v in retrieved_2.embedding)

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()

    async def test_hnsw_index_catalog_metadata(
        self, db_engine: AsyncEngine
    ) -> None:
        """Verify the HNSW index in PostgreSQL system catalog has:
        - Access method 'hnsw'
        - Opclass 'vector_cosine_ops'
        - Storage parameters m=16, ef_construction=64
        """
        async with db_engine.connect() as conn:
            res = await conn.execute(
                sa.text(
                    """
                    SELECT 
                        c.relname as index_name,
                        a.amname as access_method,
                        c.reloptions as options,
                        pg_get_indexdef(c.oid) as index_def
                    FROM pg_class c
                    JOIN pg_am a ON c.relam = a.oid
                    JOIN pg_index i ON c.oid = i.indexrelid
                    JOIN pg_class t ON i.indrelid = t.oid
                    WHERE t.relname = 'memory_chunks' AND c.relname = 'ix_memory_chunks_embedding_hnsw';
                    """
                )
            )
            row = res.fetchone()
            assert row is not None, "ix_memory_chunks_embedding_hnsw not found in pg_class"

            assert row.access_method == "hnsw", f"Expected 'hnsw' AM, got {row.access_method}"
            assert row.options == ["m=16", "ef_construction=64"], f"Unexpected options: {row.options}"
            assert "vector_cosine_ops" in row.index_def

    async def test_hnsw_query_plan_and_session_tuning(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify:
        1. EXPLAIN query on cosine distance (<=>) with enable_seqscan=off uses Index Scan on HNSW index.
        2. Session parameter SET LOCAL hnsw.ef_search operates cleanly.
        3. Cosine distance returns exact 0.0 for identical vector.
        """
        async with session_factory() as session:
            user_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="HNSW Query User"))
            await session.commit()

            try:
                # Insert 10 memory chunks
                query_vec = [0.01 * (i % 50) for i in range(384)]
                vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

                for idx in range(10):
                    vec = [0.01 * ((i + idx) % 50) for i in range(384)]
                    session.add(
                        MemoryChunkModel(
                            id=uuid.uuid4(),
                            user_id=user_id,
                            scope="user",
                            content=f"Chunk index {idx}",
                            embedding=vec,
                            embedding_model="text-embedding-3-small",
                        )
                    )
                await session.commit()

                # Test runtime tuning of hnsw.ef_search
                await session.execute(sa.text("SET LOCAL hnsw.ef_search = 100;"))

                # Test query execution and cosine distance accuracy
                query_res = await session.execute(
                    sa.text(
                        "SELECT content, (embedding <=> CAST(:qemb AS vector)) as distance "
                        "FROM memory_chunks WHERE user_id = :uid ORDER BY distance ASC LIMIT 3;"
                    ),
                    {"uid": user_id, "qemb": vec_str},
                )
                top_rows = query_res.fetchall()
                assert len(top_rows) == 3
                # First match should be Chunk index 0 (identical embedding, distance ~ 0.0)
                assert top_rows[0].content == "Chunk index 0"
                assert abs(top_rows[0].distance) < 1e-5

                # Verify EXPLAIN query uses index scan when seqscan is off
                await session.execute(sa.text("SET LOCAL enable_seqscan = off;"))
                explain_res = await session.execute(
                    sa.text(
                        "EXPLAIN SELECT content FROM memory_chunks ORDER BY embedding <=> CAST(:qemb AS vector) LIMIT 5;"
                    ),
                    {"qemb": vec_str},
                )
                explain_lines = [r[0] for r in explain_res.fetchall()]
                plan_text = " ".join(explain_lines)
                assert "ix_memory_chunks_embedding_hnsw" in plan_text
                assert "Index Scan" in plan_text

            finally:
                await session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await session.commit()


# ==============================================================================
# 4. CONCURRENT STRESS HARNESS & BATCH OPERATIONS
# ==============================================================================


@pytest.mark.asyncio
class TestSchemaStressHarness:
    """Stress-test concurrent writes, bulk vector operations, and server-level defaults."""

    async def test_concurrent_batch_memory_chunks_and_queries(
        self, session_factory: sessionmaker
    ) -> None:
        """Spawn 5 concurrent writer tasks each writing 10 384-dim vectors and querying nearest neighbors."""
        async with session_factory() as session:
            user_id = uuid.uuid4()
            session.add(UserModel(id=user_id, display_name="Stress User"))
            await session.commit()

        async def worker_stress(task_id: int) -> int:
            async with session_factory() as s:
                # 1. Insert 10 chunks
                for i in range(10):
                    vec = [0.005 * ((i * 7 + task_id * 13 + j) % 100) for j in range(384)]
                    chunk = MemoryChunkModel(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        scope=f"task_{task_id}",
                        content=f"Stress content {task_id}_{i}",
                        embedding=vec,
                        embedding_model="test-embedder",
                    )
                    s.add(chunk)
                await s.commit()

                # 2. Query nearest 3 chunks
                q_vec = [0.005 * ((task_id * 13 + j) % 100) for j in range(384)]
                q_str = "[" + ",".join(str(v) for v in q_vec) + "]"
                res = await s.execute(
                    sa.text(
                        "SELECT content FROM memory_chunks "
                        "WHERE user_id = :uid ORDER BY embedding <=> CAST(:q AS vector) LIMIT 3;"
                    ),
                    {"uid": user_id, "q": q_str},
                )
                return len(res.fetchall())

        try:
            results = await asyncio.gather(*[worker_stress(t) for t in range(5)])
            assert results == [3, 3, 3, 3, 3]

            async with session_factory() as verify_session:
                count_res = await verify_session.execute(
                    sa.text("SELECT count(*) FROM memory_chunks WHERE user_id = :uid;"),
                    {"uid": user_id},
                )
                assert count_res.scalar() == 50
        finally:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": user_id}
                )
                await cleanup_session.commit()

    async def test_server_default_values(
        self, session_factory: sessionmaker
    ) -> None:
        """Verify database-level server defaults for settings, status, attempts, and excluded."""
        async with session_factory() as session:
            # 1. User settings defaults to '{}'
            uid = uuid.uuid4()
            await session.execute(
                sa.text("INSERT INTO users (id, display_name) VALUES (:id, 'Default User');"),
                {"id": uid},
            )
            await session.commit()
            user = await session.get(UserModel, uid)
            assert user is not None
            assert user.settings == {}

            # 2. ConnectedAccount status defaults to 'active', scopes & tools to '[]'
            cid = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO connected_accounts (id, user_id, connector_id, account_label, encrypted_credentials) "
                    "VALUES (:id, :uid, 'gmail', 'lbl', 'enc');"
                ),
                {"id": cid, "uid": uid},
            )
            await session.commit()
            account = await session.get(ConnectedAccountModel, cid)
            assert account is not None
            assert account.status == "active"
            assert account.scopes == []

            # 2b. Connector tool defaults to enabled = true, and the level is constrained
            tid = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO connector_tools (id, connector_account_id, tool_name, level) "
                    "VALUES (:id, :cid, 'gmail_search', 'read');"
                ),
                {"id": tid, "cid": cid},
            )
            await session.commit()
            tool = await session.get(ConnectorToolModel, tid)
            assert tool is not None
            assert tool.enabled is True
            assert tool.level == "read"

            # 3. Job defaults: status='pending', attempts=0, max_attempts=5, payload='{}'
            jid = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO jobs (id, type, run_at) "
                    "VALUES (:id, 'test_job', now() + interval '1 day');"
                ),
                {"id": jid},
            )
            await session.commit()
            job = await session.get(JobModel, jid)
            assert job is not None
            assert job.status == "pending"
            assert job.attempts == 0
            assert job.max_attempts == 5
            assert job.payload == {}

            # 4. Memory chunk excluded defaults to false
            mid = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO memory_chunks (id, user_id, scope, content, embedding, embedding_model) "
                    "VALUES (:id, :uid, 'test', 'cnt', CAST(:emb AS vector), 'mod');"
                ),
                {"id": mid, "uid": uid, "emb": "[" + ",".join(["0.0"] * 384) + "]"},
            )
            await session.commit()
            chunk = await session.get(MemoryChunkModel, mid)
            assert chunk is not None
            assert chunk.excluded is False

            # Cleanup
            await session.execute(
                sa.text("DELETE FROM jobs WHERE id = :jid;"), {"jid": jid}
            )
            await session.execute(
                sa.text("DELETE FROM users WHERE id = :uid;"), {"uid": uid}
            )
            await session.commit()
