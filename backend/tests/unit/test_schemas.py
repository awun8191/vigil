"""Unit tests validating all 16 SQLAlchemy 2.0 declarative models and metadata contracts."""
from __future__ import annotations

import uuid
import pytest
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.schemas import (
    ApprovalModel,
    ArtifactModel,
    Base,
    CardActionModel,
    CardModel,
    ConnectedAccountModel,
    ConnectorItemModel,
    ConnectorSyncStateModel,
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

EXPECTED_TABLES = {
    "users",
    "devices",
    "connected_accounts",
    "connector_items",
    "connector_sync_state",
    "triage_decisions",
    "cards",
    "card_actions",
    "threads",
    "runs",
    "messages",
    "approvals",
    "artifacts",
    "memory_chunks",
    "jobs",
    "idempotency_records",
}


def test_central_schema_exports_all_sixteen_tables() -> None:
    """Verify that exactly 16 tables are registered in the centralized Base metadata."""
    registered_tables = set(Base.metadata.tables.keys())
    assert registered_tables == EXPECTED_TABLES
    assert len(registered_tables) == 16


def test_models_map_to_expected_table_names() -> None:
    """Verify each model class maps to its expected table name."""
    model_table_map = {
        UserModel: "users",
        DeviceModel: "devices",
        ConnectedAccountModel: "connected_accounts",
        ConnectorItemModel: "connector_items",
        ConnectorSyncStateModel: "connector_sync_state",
        TriageDecisionModel: "triage_decisions",
        CardModel: "cards",
        CardActionModel: "card_actions",
        ThreadModel: "threads",
        RunModel: "runs",
        MessageModel: "messages",
        ApprovalModel: "approvals",
        ArtifactModel: "artifacts",
        MemoryChunkModel: "memory_chunks",
        JobModel: "jobs",
        IdempotencyRecordModel: "idempotency_records",
    }
    for model, expected_table in model_table_map.items():
        assert model.__tablename__ == expected_table
        assert model.__table__.name == expected_table


def test_primary_keys() -> None:
    """Verify primary key columns across all 16 models."""
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        pk_cols = [c.name for c in table.primary_key.columns]
        if table_name == "connector_sync_state":
            assert pk_cols == ["connector_account_id"]
        elif table_name == "idempotency_records":
            assert pk_cols == ["key"]
        else:
            assert pk_cols == ["id"]


def test_foreign_key_cascades_and_set_null() -> None:
    """Verify cascade delete and set null rules on all foreign keys."""
    # 13 foreign keys must specify CASCADE
    expected_cascade_fks = {
        ("devices", "user_id"): "users.id",
        ("connected_accounts", "user_id"): "users.id",
        ("connector_items", "connector_account_id"): "connected_accounts.id",
        ("connector_sync_state", "connector_account_id"): "connected_accounts.id",
        ("triage_decisions", "item_id"): "connector_items.id",
        ("card_actions", "card_id"): "cards.id",
        ("threads", "user_id"): "users.id",
        ("messages", "session_id"): "threads.id",
        ("messages", "run_id"): "runs.id",
        ("approvals", "run_id"): "runs.id",
        ("artifacts", "run_id"): "runs.id",
        ("memory_chunks", "user_id"): "users.id",
        ("cards", "user_id"): "users.id",
    }
    for (tbl_name, col_name), target in expected_cascade_fks.items():
        tbl = Base.metadata.tables[tbl_name]
        fk = next((fk for fk in tbl.foreign_keys if fk.parent.name == col_name), None)
        assert fk is not None, f"Missing FK on {tbl_name}.{col_name}"
        assert fk.target_fullname == target
        assert fk.ondelete.upper() == "CASCADE"

    # 3 foreign keys must specify SET NULL
    expected_set_null_fks = {
        ("cards", "item_id"): "connector_items.id",
        ("runs", "card_id"): "cards.id",
        ("runs", "parent_run_id"): "runs.id",
    }
    for (tbl_name, col_name), target in expected_set_null_fks.items():
        tbl = Base.metadata.tables[tbl_name]
        fk = next((fk for fk in tbl.foreign_keys if fk.parent.name == col_name), None)
        assert fk is not None, f"Missing FK on {tbl_name}.{col_name}"
        assert fk.target_fullname == target
        assert fk.ondelete.upper() == "SET NULL"


def test_unique_constraints() -> None:
    """Verify unique constraints and columns."""
    # devices: token_hash unique
    devices = Base.metadata.tables["devices"]
    assert devices.c.token_hash.unique is True

    # connected_accounts: (user_id, connector_id, account_label)
    conn_acc = Base.metadata.tables["connected_accounts"]
    conn_uq = next(
        (uq for uq in conn_acc.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_connected_accounts_user_conn_label"),
        None,
    )
    assert conn_uq is not None
    assert [c.name for c in conn_uq.columns] == ["user_id", "connector_id", "account_label"]

    # connector_items: (connector_account_id, external_id)
    conn_items = Base.metadata.tables["connector_items"]
    items_uq = next(
        (uq for uq in conn_items.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_connector_items_account_external_id"),
        None,
    )
    assert items_uq is not None
    assert [c.name for c in items_uq.columns] == ["connector_account_id", "external_id"]

    # triage_decisions: item_id unique
    triage = Base.metadata.tables["triage_decisions"]
    assert triage.c.item_id.unique is True


def test_memory_chunks_vector_type_and_hnsw_index() -> None:
    """Verify pgvector Vector(1536) and HNSW cosine distance index on memory_chunks."""
    chunks = Base.metadata.tables["memory_chunks"]
    emb_col = chunks.c.embedding
    assert isinstance(emb_col.type, Vector)
    assert emb_col.type.dim == 1536

    hnsw_idx = next((idx for idx in chunks.indexes if idx.name == "ix_memory_chunks_embedding_hnsw"), None)
    assert hnsw_idx is not None
    assert hnsw_idx.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw_idx.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}
    assert hnsw_idx.dialect_options["postgresql"]["with"] == {"m": 16, "ef_construction": 64}


def test_composite_indexes() -> None:
    """Verify performance composite indexes on jobs, cards, messages, memory_chunks, and connector_items."""
    # jobs
    jobs = Base.metadata.tables["jobs"]
    claim_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_claim_pending"), None)
    assert claim_idx is not None
    assert [c.name for c in claim_idx.columns] == ["status", "run_at"]

    stale_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_stale_locked"), None)
    assert stale_idx is not None
    assert [c.name for c in stale_idx.columns] == ["status", "locked_at"]

    # cards
    cards = Base.metadata.tables["cards"]
    card_prio_idx = next((idx for idx in cards.indexes if idx.name == "ix_cards_user_status_urgency"), None)
    assert card_prio_idx is not None
    assert [c.name for c in card_prio_idx.columns] == ["user_id", "status", "urgency"]

    card_due_idx = next((idx for idx in cards.indexes if idx.name == "ix_cards_user_due_at"), None)
    assert card_due_idx is not None
    assert [c.name for c in card_due_idx.columns] == ["user_id", "due_at"]

    # messages
    messages = Base.metadata.tables["messages"]
    msg_session_idx = next((idx for idx in messages.indexes if idx.name == "ix_messages_session_created"), None)
    assert msg_session_idx is not None
    assert [c.name for c in msg_session_idx.columns] == ["session_id", "created_at"]

    msg_run_idx = next((idx for idx in messages.indexes if idx.name == "ix_messages_run_created"), None)
    assert msg_run_idx is not None
    assert [c.name for c in msg_run_idx.columns] == ["run_id", "created_at"]

    # memory_chunks
    mem_idx = next((idx for idx in Base.metadata.tables["memory_chunks"].indexes if idx.name == "ix_memory_chunks_user_scope_excluded"), None)
    assert mem_idx is not None
    assert [c.name for c in mem_idx.columns] == ["user_id", "scope", "excluded"]

    # connector_items
    item_idx = next((idx for idx in Base.metadata.tables["connector_items"].indexes if idx.name == "ix_connector_items_account_received"), None)
    assert item_idx is not None
    assert [c.name for c in item_idx.columns] == ["connector_account_id", "received_at"]


def test_relationships_configuration() -> None:
    """Verify key ORM relationships (bidirectional, cascades, self-referential)."""
    # UserModel relationships
    user_mapper = sa.inspect(UserModel)
    assert "devices" in user_mapper.relationships
    assert "connected_accounts" in user_mapper.relationships
    assert "cards" in user_mapper.relationships
    assert "threads" in user_mapper.relationships
    assert "memory_chunks" in user_mapper.relationships

    # RunModel self-referential relationship
    run_mapper = sa.inspect(RunModel)
    assert "parent_run" in run_mapper.relationships
    assert "child_runs" in run_mapper.relationships
    parent_rel = run_mapper.relationships["parent_run"]
    assert parent_rel.remote_side == {RunModel.__table__.c.id}

    # MessageModel dual relationships
    msg_mapper = sa.inspect(MessageModel)
    assert "thread" in msg_mapper.relationships
    assert "run" in msg_mapper.relationships

    # CardModel and CardActionModel
    card_mapper = sa.inspect(CardModel)
    assert "actions" in card_mapper.relationships
    assert card_mapper.relationships["actions"].lazy == "selectin"
