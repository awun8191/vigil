"""Schema corrections: 384-dimension embeddings, integer money, queue priority, snapshots.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31 22:30:00.000000

This revision applies the seven approved corrections to the draft schema. Revision
0001 has never run against a database that holds rows, so the embedding column can
change width in place. The upgrade drops and rebuilds the HNSW index around the
type change, and it assumes that memory_chunks holds no rows.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Revision 0001 created the embedding column with a wider vector. The downgrade
# restores that width. The forward path never uses it.
PREVIOUS_EMBEDDING_DIM = 1536

MEMORY_SCOPE_CHECK = (
    "(scope <> 'card' OR card_id IS NOT NULL) "
    "AND (scope <> 'agent' OR agent_config_id IS NOT NULL)"
)


def upgrade() -> None:
    """Apply the correction set to all affected tables."""
    # 1. memory_chunks: 384-dimension embeddings, embedding metadata, scope parents.
    op.drop_index("ix_memory_chunks_embedding_hnsw", table_name="memory_chunks")
    op.execute("ALTER TABLE memory_chunks ALTER COLUMN embedding TYPE vector(384)")
    op.create_index(
        "ix_memory_chunks_embedding_hnsw",
        "memory_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.add_column(
        "memory_chunks",
        sa.Column("embedding_dim", sa.Integer(), nullable=False, server_default=sa.text("384")),
    )
    op.add_column(
        "memory_chunks",
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_memory_chunks_card_id_cards"),
        "memory_chunks",
        "cards",
        ["card_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_memory_chunks_card_id", "memory_chunks", ["card_id"], unique=False)
    op.add_column(
        "memory_chunks",
        sa.Column("agent_config_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_memory_chunks_scope_parent"), "memory_chunks", MEMORY_SCOPE_CHECK
    )

    # 2. runs: integer money in micro-USD and the snapshot pointer.
    op.add_column(
        "runs",
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("runs", sa.Column("workspace_ref", sa.Text(), nullable=True))
    op.drop_column("runs", "cost_usd")

    # 3. run_workspaces: the snapshot archive that a run owns.
    op.create_table(
        "run_workspaces",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_run_workspaces_run_id_runs",
            ondelete="CASCADE",
        ),
    )

    # 4. messages: the chunking marker and its partial index.
    op.add_column(
        "messages",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_messages_unindexed",
        "messages",
        ["indexed_at"],
        unique=False,
        postgresql_where=sa.text("indexed_at IS NULL"),
    )

    # 5. approvals: one approval row per tool call in a run.
    op.create_unique_constraint(
        op.f("uq_approvals_run_tool_call"), "approvals", ["run_id", "tool_call_id"]
    )

    # 6. connector_tools: a tool name carries its own approval level.
    op.create_table(
        "connector_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connector_account_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.ForeignKeyConstraint(
            ["connector_account_id"],
            ["connected_accounts.id"],
            name="fk_connector_tools_connector_account_id_connected_accounts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "connector_account_id", "tool_name", name="uq_connector_tools_account_tool"
        ),
        # The model passes the bare suffix because the "ck" naming convention
        # adds the ck_connector_tools_ prefix.
        sa.CheckConstraint(
            "level IN ('read', 'local', 'external')", name="level"
        ),
    )
    op.drop_column("connected_accounts", "enabled_tools")

    # 7. jobs: priority ordering and the heartbeat reaper.
    op.drop_index("ix_jobs_stale_locked", table_name="jobs")
    op.drop_index("ix_jobs_claim_pending", table_name="jobs")
    op.add_column(
        "jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_jobs_claim_pending ON jobs (priority DESC, run_at) "
        "WHERE status = 'pending'"
    )
    op.create_index(
        "ix_jobs_stale_heartbeat", "jobs", ["status", "heartbeat_at"], unique=False
    )

    # 8. triage_decisions: a re-triage is legal.
    op.drop_index("ix_triage_decisions_item_id", table_name="triage_decisions")
    op.create_index(
        "ix_triage_decisions_item_id", "triage_decisions", ["item_id"], unique=False
    )
    op.create_unique_constraint(
        op.f("uq_triage_decisions_item_model"),
        "triage_decisions",
        ["item_id", "model_version"],
    )

    # 9. card_actions: a card cannot repeat an action key.
    op.create_unique_constraint(
        op.f("uq_card_actions_card_key"), "card_actions", ["card_id", "action_key"]
    )


def downgrade() -> None:
    """Reverse every correction in this revision."""
    # 9. card_actions
    op.drop_constraint(
        op.f("uq_card_actions_card_key"), "card_actions", type_="unique"
    )

    # 8. triage_decisions
    op.drop_constraint(
        op.f("uq_triage_decisions_item_model"), "triage_decisions", type_="unique"
    )
    op.drop_index("ix_triage_decisions_item_id", table_name="triage_decisions")
    op.create_index(
        "ix_triage_decisions_item_id",
        "triage_decisions",
        ["item_id"],
        unique=True,
    )

    # 7. jobs
    op.drop_index("ix_jobs_stale_heartbeat", table_name="jobs")
    op.drop_index("ix_jobs_claim_pending", table_name="jobs")
    op.create_index(
        "ix_jobs_claim_pending", "jobs", ["status", "run_at"], unique=False
    )
    op.create_index("ix_jobs_stale_locked", "jobs", ["status", "locked_at"], unique=False)
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "priority")

    # 6. connector_tools
    op.drop_table("connector_tools")
    op.add_column(
        "connected_accounts",
        sa.Column(
            "enabled_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # 5. approvals
    op.drop_constraint(
        op.f("uq_approvals_run_tool_call"), "approvals", type_="unique"
    )

    # 4. messages
    op.drop_index("ix_messages_unindexed", table_name="messages")
    op.drop_column("messages", "indexed_at")

    # 3. run_workspaces
    op.drop_table("run_workspaces")

    # 2. runs
    op.add_column(
        "runs",
        sa.Column(
            "cost_usd",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.drop_column("runs", "workspace_ref")
    op.drop_column("runs", "cost_micros")

    # 1. memory_chunks
    op.drop_constraint(
        op.f("ck_memory_chunks_scope_parent"), "memory_chunks", type_="check"
    )
    op.drop_column("memory_chunks", "agent_config_id")
    op.drop_index("ix_memory_chunks_card_id", table_name="memory_chunks")
    op.drop_constraint(
        op.f("fk_memory_chunks_card_id_cards"), "memory_chunks", type_="foreignkey"
    )
    op.drop_column("memory_chunks", "card_id")
    op.drop_column("memory_chunks", "embedding_dim")
    op.drop_index("ix_memory_chunks_embedding_hnsw", table_name="memory_chunks")
    op.execute(
        f"DELETE FROM memory_chunks WHERE vector_dims(embedding) != {PREVIOUS_EMBEDDING_DIM}"
    )
    op.execute(
        "ALTER TABLE memory_chunks ALTER COLUMN embedding "
        f"TYPE vector({PREVIOUS_EMBEDDING_DIM})"
    )
    op.create_index(
        "ix_memory_chunks_embedding_hnsw",
        "memory_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
