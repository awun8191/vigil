"""Skills schema: the durable registry of installed capability skills.

A skill is a directory that holds a ``SKILL.md`` manifest with YAML frontmatter
and optional scripts. The registry lives in PostgreSQL so an installed skill
survives a process restart. ``storage_path`` points at the extracted copy under
the configured skills root. It never points at a Git working tree.

Decision 4 of S-01 fixes the uniqueness: ``name`` is unique and the pair
``(name, version)`` is unique. Enable and disable change ``enabled`` only, so a
disabled skill keeps its row and its files.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.schemas.base import Base

#: The source values that the table accepts. A builtin skill ships with the
#: image, a git skill comes from a pinned commit, an upload skill comes from a
#: user archive.
SKILL_SOURCES: tuple[str, ...] = ("builtin", "git", "upload")


class SkillModel(Base):
    """One installed skill in the durable registry.

    ``name`` and ``(name, version)`` are both unique. ``commit`` holds the
    resolved 40-character SHA for a Git install and is null for a builtin skill.
    ``storage_path`` is the extracted directory under the skills root.
    """

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="builtin", server_default="builtin"
    )
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_skills_name"),
        UniqueConstraint("name", "version", name="uq_skills_name_version"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "source IN ('builtin', 'git', 'upload')", name="source_allowed"
        ),
        Index("ix_skills_enabled", "enabled"),
    )


__all__ = ["SKILL_SOURCES", "SkillModel"]
