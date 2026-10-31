"""Integration tests verifying Alembic pgvector migrations upgrade and rollback lifecycle."""
from __future__ import annotations

import io
import os
from pathlib import Path
import uuid
import pytest
from alembic.config import Config
from alembic import command
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings


@pytest.fixture
def alembic_config() -> Config:
    """Return configured Alembic Config pointing to backend/alembic.ini."""
    backend_dir = Path(__file__).resolve().parent.parent.parent
    ini_path = backend_dir / "alembic.ini"
    assert ini_path.exists(), f"alembic.ini not found at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


EXPECTED_TABLES = [
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
    "runs",
    "run_workspaces",
    "messages",
    "approvals",
    "artifacts",
    "memory_chunks",
    "jobs",
    "idempotency_records",
]


def test_alembic_offline_sql_generation(alembic_config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """Validate that offline SQL DDL generation produces all 18 tables, pgvector extension, and HNSW index."""
    output_buffer = io.StringIO()
    alembic_config.stdout = output_buffer

    command.upgrade(alembic_config, "head", sql=True)
    captured = capsys.readouterr()
    sql_text = output_buffer.getvalue() or captured.out

    # Verify pgvector extension
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql_text

    # Verify all 18 tables exist in generated SQL
    for tbl in EXPECTED_TABLES:
        assert f"CREATE TABLE {tbl}" in sql_text

    # Verify HNSW index and vector_cosine_ops
    assert "ix_memory_chunks_embedding_hnsw" in sql_text
    assert "hnsw" in sql_text.lower()
    assert "vector_cosine_ops" in sql_text

    # Verify the corrections land in 0002
    assert "ALTER TABLE memory_chunks ALTER COLUMN embedding TYPE vector(384)" in sql_text
    assert "embedding_dim INTEGER DEFAULT 384 NOT NULL" in sql_text
    assert "cost_micros BIGINT DEFAULT 0 NOT NULL" in sql_text
    assert "DROP COLUMN cost_usd" in sql_text
    assert "ADD COLUMN indexed_at TIMESTAMP WITH TIME ZONE" in sql_text
    assert "ix_messages_unindexed ON messages (indexed_at) WHERE indexed_at IS NULL" in sql_text
    assert "ADD COLUMN priority INTEGER DEFAULT 0 NOT NULL" in sql_text
    assert "ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE" in sql_text
    assert "CREATE INDEX ix_jobs_claim_pending ON jobs (priority DESC, run_at) WHERE status = 'pending'" in sql_text
    assert "CREATE INDEX ix_jobs_stale_heartbeat ON jobs (status, heartbeat_at)" in sql_text
    assert "CHECK (level IN ('read', 'local', 'external'))" in sql_text
    assert "UNIQUE (item_id, model_version)" in sql_text
    assert "DROP COLUMN enabled_tools" in sql_text


@pytest.mark.asyncio
async def test_alembic_live_upgrade_downgrade_lifecycle(alembic_config: Config) -> None:
    """
    Test live database migration lifecycle against PostgreSQL container:
    1. Upgrade to head
    2. Verify 18 tables, vector extension, HNSW index, and the corrected columns
    3. Downgrade to base
    4. Verify all 18 tables are cleanly dropped
    5. Upgrade to head again (verifying idempotency)
    """
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)

    # Test database connectivity; skip if PostgreSQL is unavailable
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1;"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    try:
        # 1. Upgrade to head
        command.upgrade(alembic_config, "head")

        # 2. Inspect created tables and extensions
        async with engine.connect() as conn:
            res = await conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
                )
            )
            tables = {row[0] for row in res.fetchall()}
            assert "alembic_version" in tables
            assert set(EXPECTED_TABLES).issubset(tables), f"Missing tables: {set(EXPECTED_TABLES) - tables}"

            # Verify vector extension
            ext_res = await conn.execute(
                sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            )
            assert ext_res.scalar() == "vector"

            # Verify HNSW index
            idx_res = await conn.execute(
                sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_memory_chunks_embedding_hnsw';")
            )
            idx_def = idx_res.scalar()
            assert idx_def is not None
            assert "hnsw" in idx_def
            assert "vector_cosine_ops" in idx_def

            # Verify the 384-dimension embedding column
            emb_res = await conn.execute(
                sa.text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'memory_chunks'::regclass AND attname = 'embedding';"
                )
            )
            assert emb_res.scalar() == "vector(384)"

            # Verify the corrected runs columns
            runs_res = await conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'runs';"
                )
            )
            run_columns = {row[0] for row in runs_res.fetchall()}
            assert "cost_micros" in run_columns
            assert "workspace_ref" in run_columns
            assert "cost_usd" not in run_columns

            # Verify the queue and chunking columns
            jobs_res = await conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'jobs';"
                )
            )
            job_columns = {row[0] for row in jobs_res.fetchall()}
            assert "priority" in job_columns
            assert "heartbeat_at" in job_columns

            msg_res = await conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'messages';"
                )
            )
            assert "indexed_at" in {row[0] for row in msg_res.fetchall()}

            # Verify the new constraints
            constraint_res = await conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conname IN ("
                    "'ck_memory_chunks_scope_parent', 'ck_connector_tools_level', "
                    "'uq_triage_decisions_item_model', 'uq_card_actions_card_key', "
                    "'uq_approvals_run_tool_call');"
                )
            )
            constraint_names = {row[0] for row in constraint_res.fetchall()}
            assert constraint_names == {
                "ck_memory_chunks_scope_parent",
                "ck_connector_tools_level",
                "uq_triage_decisions_item_model",
                "uq_card_actions_card_key",
                "uq_approvals_run_tool_call",
            }

        # 3. Downgrade to base
        command.downgrade(alembic_config, "base")

        # 4. Verify all 18 domain tables were dropped
        async with engine.connect() as conn:
            res_after = await conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name != 'alembic_version';"
                )
            )
            remaining_tables = [row[0] for row in res_after.fetchall()]
            assert len(remaining_tables) == 0, f"Remaining tables after downgrade: {remaining_tables}"

        # 5. Upgrade to head again (verifying clean re-application)
        command.upgrade(alembic_config, "head")
        async with engine.connect() as conn:
            res_reapply = await conn.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name != 'alembic_version';"
                )
            )
            count = res_reapply.scalar()
            assert count == 18, f"Expected 18 tables on re-upgrade, found {count}"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_referential_cascades_and_pgvector_query(alembic_config: Config) -> None:
    """Verify foreign key cascading behavior and pgvector cosine distance query on live PostgreSQL."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)

    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1;"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    try:
        # Ensure schema is upgraded
        command.upgrade(alembic_config, "head")

        async_session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async with async_session_factory() as session:
            # 1. Insert user
            user_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, display_name, settings) "
                    "VALUES (:id, :name, :settings);"
                ),
                {"id": user_id, "name": "Test User", "settings": "{}"},
            )

            # 2. Insert connected account
            conn_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO connected_accounts (id, user_id, connector_id, account_label, encrypted_credentials) "
                    "VALUES (:id, :uid, :cid, :label, :creds);"
                ),
                {"id": conn_id, "uid": user_id, "cid": "gmail", "label": "test@gmail.com", "creds": "encrypted"},
            )

            # 3. Insert connector item
            item_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO connector_items (id, connector_account_id, external_id, thread_id, sender, subject, body_text, received_at) "
                    "VALUES (:id, :caid, :eid, :tid, :snd, :sub, :body, now());"
                ),
                {"id": item_id, "caid": conn_id, "eid": "ext-1", "tid": "thread-1", "snd": "sender", "sub": "subject", "body": "body"},
            )

            # 4. Insert card referencing item_id
            card_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO cards (id, user_id, item_id, type, urgency, title) "
                    "VALUES (:id, :uid, :iid, :typ, :urg, :title);"
                ),
                {"id": card_id, "uid": user_id, "iid": item_id, "typ": "email", "urg": "high", "title": "Card Title"},
            )

            # 5. Insert memory chunk with a 384-dim vector
            chunk_id = uuid.uuid4()
            vector_data = [0.01 * (i % 10) for i in range(384)]
            vector_str = "[" + ",".join(str(v) for v in vector_data) + "]"
            await session.execute(
                sa.text(
                    "INSERT INTO memory_chunks (id, user_id, scope, content, embedding, embedding_model) "
                    "VALUES (:id, :uid, :scope, :content, CAST(:emb AS vector), :model);"
                ),
                {"id": chunk_id, "uid": user_id, "scope": "user", "content": "Test memory content", "emb": vector_str, "model": "bge-small-en-v1.5"},
            )

            await session.commit()

            # Verify vector similarity query using cosine distance (<=>)
            query_res = await session.execute(
                sa.text(
                    "SELECT content, (embedding <=> CAST(:qemb AS vector)) as distance "
                    "FROM memory_chunks WHERE user_id = :uid ORDER BY distance ASC LIMIT 1;"
                ),
                {"uid": user_id, "qemb": vector_str},
            )
            row = query_res.fetchone()
            assert row is not None
            assert row[0] == "Test memory content"
            assert abs(row[1]) < 1e-5  # Distance from itself should be ~0.0

            # 6. Delete connector_item -> verify cards.item_id becomes NULL (SET NULL)
            await session.execute(
                sa.text("DELETE FROM connector_items WHERE id = :id;"),
                {"id": item_id},
            )
            await session.commit()

            card_res = await session.execute(
                sa.text("SELECT item_id FROM cards WHERE id = :id;"),
                {"id": card_id},
            )
            card_item_id = card_res.scalar()
            assert card_item_id is None, "Expected card.item_id to be SET NULL upon connector_item deletion"

            # 7. Delete user -> verify cascade deletion of connected_accounts, cards, memory_chunks
            await session.execute(
                sa.text("DELETE FROM users WHERE id = :id;"),
                {"id": user_id},
            )
            await session.commit()

            ca_count = await session.execute(
                sa.text("SELECT count(*) FROM connected_accounts WHERE id = :id;"),
                {"id": conn_id},
            )
            assert ca_count.scalar() == 0, "Expected connected_account to be CASCADE deleted"

            c_count = await session.execute(
                sa.text("SELECT count(*) FROM cards WHERE id = :id;"),
                {"id": card_id},
            )
            assert c_count.scalar() == 0, "Expected card to be CASCADE deleted"

            m_count = await session.execute(
                sa.text("SELECT count(*) FROM memory_chunks WHERE id = :id;"),
                {"id": chunk_id},
            )
            assert m_count.scalar() == 0, "Expected memory_chunk to be CASCADE deleted"

    finally:
        await engine.dispose()
