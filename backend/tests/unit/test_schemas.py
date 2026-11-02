"""Unit tests validating all 20 SQLAlchemy 2.0 declarative models and metadata contracts."""
from __future__ import annotations

import uuid
import pytest
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.schema import CreateIndex

from src.schemas import (
    AgentConfigModel,
    ApprovalModel,
    ArtifactModel,
    AuditLogModel,
    Base,
    CardActionModel,
    CardModel,
    ConnectedAccountModel,
    ConnectorItemModel,
    ConnectorSyncStateModel,
    ConnectorToolModel,
    DeviceModel,
    IdempotencyRecordModel,
    JobModel,
    MCPServerModel,
    MemoryChunkModel,
    MessageModel,
    RunModel,
    RunWorkspaceModel,
    SkillModel,
    ThreadModel,
    TriageDecisionModel,
    UserModel,
)

EXPECTED_TABLES = {
    "users",
    "devices",
    "connected_accounts",
    "connector_tools",
    "connector_items",
    "connector_sync_state",
    "triage_decisions",
    "cards",
    "card_actions",
    "threads",
    "agent_configs",
    "runs",
    "run_workspaces",
    "messages",
    "approvals",
    "artifacts",
    "memory_chunks",
    "jobs",
    "idempotency_records",
    "skills",
    "audit_log",
    "mcp_servers",
}


def test_central_schema_exports_all_tables() -> None:
    """Verify that exactly 22 tables are registered in the centralized Base metadata."""
    registered_tables = set(Base.metadata.tables.keys())
    assert registered_tables == EXPECTED_TABLES
    assert len(registered_tables) == 22


def test_models_map_to_expected_table_names() -> None:
    """Verify each model class maps to its expected table name."""
    model_table_map = {
        UserModel: "users",
        DeviceModel: "devices",
        ConnectedAccountModel: "connected_accounts",
        ConnectorToolModel: "connector_tools",
        ConnectorItemModel: "connector_items",
        ConnectorSyncStateModel: "connector_sync_state",
        TriageDecisionModel: "triage_decisions",
        CardModel: "cards",
        CardActionModel: "card_actions",
        ThreadModel: "threads",
        RunModel: "runs",
        RunWorkspaceModel: "run_workspaces",
        MessageModel: "messages",
        ApprovalModel: "approvals",
        ArtifactModel: "artifacts",
        MemoryChunkModel: "memory_chunks",
        JobModel: "jobs",
        IdempotencyRecordModel: "idempotency_records",
        AgentConfigModel: "agent_configs",
        SkillModel: "skills",
        AuditLogModel: "audit_log",
        MCPServerModel: "mcp_servers",    }
    for model, expected_table in model_table_map.items():
        assert model.__tablename__ == expected_table
        assert model.__table__.name == expected_table


def test_primary_keys() -> None:
    """Verify primary key columns across all 22 models."""
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        pk_cols = [c.name for c in table.primary_key.columns]
        if table_name == "connector_sync_state":
            assert pk_cols == ["connector_account_id"]
        elif table_name == "run_workspaces":
            assert pk_cols == ["run_id"]
        elif table_name == "idempotency_records":
            assert pk_cols == ["key"]
        else:
            assert pk_cols == ["id"]


def test_foreign_key_cascades_and_set_null() -> None:
    """Verify cascade delete and set null rules on all foreign keys."""
    # 16 foreign keys must specify CASCADE
    expected_cascade_fks = {
        ("devices", "user_id"): "users.id",
        ("connected_accounts", "user_id"): "users.id",
        ("connector_tools", "connector_account_id"): "connected_accounts.id",
        ("connector_items", "connector_account_id"): "connected_accounts.id",
        ("connector_sync_state", "connector_account_id"): "connected_accounts.id",
        ("triage_decisions", "item_id"): "connector_items.id",
        ("card_actions", "card_id"): "cards.id",
        ("threads", "user_id"): "users.id",
        ("run_workspaces", "run_id"): "runs.id",
        ("messages", "session_id"): "threads.id",
        ("messages", "run_id"): "runs.id",
        ("approvals", "run_id"): "runs.id",
        ("artifacts", "run_id"): "runs.id",
        ("memory_chunks", "user_id"): "users.id",
        ("memory_chunks", "card_id"): "cards.id",
        ("cards", "user_id"): "users.id",
    }
    for (tbl_name, col_name), target in expected_cascade_fks.items():
        tbl = Base.metadata.tables[tbl_name]
        fk = next((fk for fk in tbl.foreign_keys if fk.parent.name == col_name), None)
        assert fk is not None, f"Missing FK on {tbl_name}.{col_name}"
        assert fk.target_fullname == target
        assert fk.ondelete.upper() == "CASCADE"

    # 4 foreign keys must specify SET NULL
    expected_set_null_fks = {
        ("cards", "item_id"): "connector_items.id",
        ("runs", "card_id"): "cards.id",
        ("runs", "parent_run_id"): "runs.id",
        ("runs", "config_id"): "agent_configs.id",
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

    # connector_tools: (connector_account_id, tool_name)
    conn_tools = Base.metadata.tables["connector_tools"]
    tools_uq = next(
        (uq for uq in conn_tools.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_connector_tools_account_tool"),
        None,
    )
    assert tools_uq is not None
    assert [c.name for c in tools_uq.columns] == ["connector_account_id", "tool_name"]

    # connector_items: (connector_account_id, external_id)
    conn_items = Base.metadata.tables["connector_items"]
    items_uq = next(
        (uq for uq in conn_items.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_connector_items_account_external_id"),
        None,
    )
    assert items_uq is not None
    assert [c.name for c in items_uq.columns] == ["connector_account_id", "external_id"]

    # triage_decisions: a re-triage is legal, so uniqueness is (item_id, model_version)
    triage = Base.metadata.tables["triage_decisions"]
    assert triage.c.item_id.unique is not True
    triage_uq = next(
        (uq for uq in triage.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_triage_decisions_item_model"),
        None,
    )
    assert triage_uq is not None
    assert [c.name for c in triage_uq.columns] == ["item_id", "model_version"]
    triage_item_idx = next(
        (idx for idx in triage.indexes if idx.name == "ix_triage_decisions_item_id"),
        None,
    )
    assert triage_item_idx is not None
    assert triage_item_idx.unique is not True

    # approvals: one approval row per tool call in a run
    approvals = Base.metadata.tables["approvals"]
    approval_uq = next(
        (uq for uq in approvals.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_approvals_run_tool_call"),
        None,
    )
    assert approval_uq is not None
    assert [c.name for c in approval_uq.columns] == ["run_id", "tool_call_id"]

    # card_actions: a card cannot repeat an action key
    card_actions = Base.metadata.tables["card_actions"]
    action_uq = next(
        (uq for uq in card_actions.constraints if isinstance(uq, sa.UniqueConstraint) and uq.name == "uq_card_actions_card_key"),
        None,
    )
    assert action_uq is not None
    assert [c.name for c in action_uq.columns] == ["card_id", "action_key"]


def test_memory_chunks_vector_type_and_hnsw_index() -> None:
    """Verify pgvector Vector(384) and HNSW cosine distance index on memory_chunks."""
    chunks = Base.metadata.tables["memory_chunks"]
    emb_col = chunks.c.embedding
    assert isinstance(emb_col.type, Vector)
    assert emb_col.type.dim == 384

    hnsw_idx = next((idx for idx in chunks.indexes if idx.name == "ix_memory_chunks_embedding_hnsw"), None)
    assert hnsw_idx is not None
    assert hnsw_idx.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw_idx.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}
    assert hnsw_idx.dialect_options["postgresql"]["with"] == {"m": 16, "ef_construction": 64}


def test_memory_chunks_embedding_metadata_and_scope_parents() -> None:
    """Verify embedding_dim and the card and agent scope parent columns."""
    chunks = Base.metadata.tables["memory_chunks"]

    assert isinstance(chunks.c.embedding_dim.type, sa.Integer)
    assert chunks.c.embedding_dim.nullable is False
    assert str(chunks.c.embedding_dim.server_default.arg) == "384"

    card_fk = next((fk for fk in chunks.foreign_keys if fk.parent.name == "card_id"), None)
    assert card_fk is not None
    assert card_fk.target_fullname == "cards.id"
    assert card_fk.ondelete.upper() == "CASCADE"
    assert chunks.c.card_id.nullable is True

    # V-06 adds the agent_configs table for runs.config_id. The memory scope
    # parent keeps a plain UUID until a later card adds its foreign key.
    assert chunks.c.agent_config_id.nullable is True
    assert not any(fk.parent.name == "agent_config_id" for fk in chunks.foreign_keys)
    assert "agent_configs" in Base.metadata.tables

    checks = {
        c.name: str(c.sqltext)
        for c in chunks.constraints
        if isinstance(c, sa.CheckConstraint)
    }
    assert "ck_memory_chunks_scope_parent" in checks
    scope_check = checks["ck_memory_chunks_scope_parent"]
    assert "scope <> 'card' OR card_id IS NOT NULL" in scope_check
    assert "scope <> 'agent' OR agent_config_id IS NOT NULL" in scope_check


def test_runs_money_is_integer_micro_usd() -> None:
    """Verify runs carries integer micro-USD and the workspace pointer, not a float."""
    runs = Base.metadata.tables["runs"]
    assert isinstance(runs.c.cost_micros.type, sa.BigInteger)
    assert runs.c.cost_micros.nullable is False
    assert str(runs.c.cost_micros.server_default.arg) == "0"
    assert "cost_usd" not in runs.c
    assert isinstance(runs.c.workspace_ref.type, sa.Text)
    assert runs.c.workspace_ref.nullable is True


def test_run_workspaces_snapshot_table() -> None:
    """Verify the run_workspaces table holds the snapshot archive of a run."""
    workspaces = Base.metadata.tables["run_workspaces"]
    assert [c.name for c in workspaces.primary_key.columns] == ["run_id"]
    assert [c.name for c in workspaces.columns] == [
        "run_id",
        "archive_path",
        "size_bytes",
        "checksum",
        "created_at",
    ]
    assert isinstance(workspaces.c.archive_path.type, sa.Text)
    assert isinstance(workspaces.c.size_bytes.type, sa.BigInteger)
    assert workspaces.c.size_bytes.nullable is False
    assert isinstance(workspaces.c.checksum.type, sa.Text)
    assert workspaces.c.created_at.nullable is False
    assert str(workspaces.c.created_at.server_default.arg) == "now()"


def test_messages_indexed_marker() -> None:
    """Verify messages.indexed_at and its partial index on unindexed rows."""
    messages = Base.metadata.tables["messages"]
    assert isinstance(messages.c.indexed_at.type, sa.DateTime)
    assert messages.c.indexed_at.type.timezone is True
    assert messages.c.indexed_at.nullable is True

    unindexed_idx = next(
        (idx for idx in messages.indexes if idx.name == "ix_messages_unindexed"), None
    )
    assert unindexed_idx is not None
    assert [c.name for c in unindexed_idx.columns] == ["indexed_at"]
    where_clause = unindexed_idx.dialect_options["postgresql"]["where"]
    assert str(where_clause) == "indexed_at IS NULL"


def test_jobs_priority_and_heartbeat() -> None:
    """Verify the job priority column, the heartbeat column, and both queue indexes."""
    jobs = Base.metadata.tables["jobs"]
    assert isinstance(jobs.c.priority.type, sa.Integer)
    assert jobs.c.priority.nullable is False
    assert str(jobs.c.priority.server_default.arg) == "0"
    assert isinstance(jobs.c.heartbeat_at.type, sa.DateTime)
    assert jobs.c.heartbeat_at.type.timezone is True
    assert jobs.c.heartbeat_at.nullable is True

    claim_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_claim_pending"), None)
    assert claim_idx is not None
    assert [c.name for c in claim_idx.columns] == ["priority", "run_at"]
    claim_ddl = str(CreateIndex(claim_idx).compile(dialect=postgresql.dialect()))
    assert "priority DESC" in claim_ddl
    assert "WHERE status = 'pending'" in claim_ddl

    heartbeat_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_stale_heartbeat"), None)
    assert heartbeat_idx is not None
    assert [c.name for c in heartbeat_idx.columns] == ["status", "heartbeat_at"]

    # The reaper reads the heartbeat, never the lock timestamp.
    assert not any(idx.name == "ix_jobs_stale_locked" for idx in jobs.indexes)


def test_connector_tools_replaces_enabled_tools() -> None:
    """Verify connector_tools carries the approval level and enabled_tools is gone."""
    accounts = Base.metadata.tables["connected_accounts"]
    assert "enabled_tools" not in accounts.c

    tools = Base.metadata.tables["connector_tools"]
    assert [c.name for c in tools.columns] == [
        "id",
        "connector_account_id",
        "tool_name",
        "level",
        "enabled",
    ]
    assert isinstance(tools.c.tool_name.type, sa.String)
    assert tools.c.tool_name.type.length == 128
    assert tools.c.level.type.length == 16
    assert tools.c.enabled.nullable is False
    assert str(tools.c.enabled.server_default.arg) == "true"

    checks = {
        c.name: str(c.sqltext)
        for c in tools.constraints
        if isinstance(c, sa.CheckConstraint)
    }
    assert "ck_connector_tools_level" in checks
    level_check = checks["ck_connector_tools_level"]
    assert "'read'" in level_check
    assert "'local'" in level_check
    assert "'external'" in level_check


def test_composite_indexes() -> None:
    """Verify performance composite indexes on jobs, cards, messages, memory_chunks, and connector_items."""
    # jobs
    jobs = Base.metadata.tables["jobs"]
    claim_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_claim_pending"), None)
    assert claim_idx is not None
    assert [c.name for c in claim_idx.columns] == ["priority", "run_at"]

    stale_idx = next((idx for idx in jobs.indexes if idx.name == "ix_jobs_stale_heartbeat"), None)
    assert stale_idx is not None
    assert [c.name for c in stale_idx.columns] == ["status", "heartbeat_at"]

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

    msg_unindexed_idx = next((idx for idx in messages.indexes if idx.name == "ix_messages_unindexed"), None)
    assert msg_unindexed_idx is not None
    assert [c.name for c in msg_unindexed_idx.columns] == ["indexed_at"]

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

    # ConnectedAccountModel owns its connector tools
    account_mapper = sa.inspect(ConnectedAccountModel)
    assert "tools" in account_mapper.relationships
    assert account_mapper.relationships["tools"].mapper.class_ is ConnectorToolModel
