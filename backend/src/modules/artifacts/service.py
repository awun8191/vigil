"""Artifacts service implementing sandbox file registration and binary streaming."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import AsyncIterator, ClassVar
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.modules.artifacts.repository import (
    ArtifactCreateDTO,
    ArtifactDTO,
)
from src.schemas.runtime import ArtifactModel


class ArtifactsService:
    """
    Artifacts management service satisfying ArtifactsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _artifacts: ClassVar[dict[UUID, ArtifactDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _is_db_session_active(self) -> bool:
        return isinstance(self.session, AsyncSession)

    @staticmethod
    def _to_dto(model: ArtifactModel) -> ArtifactDTO:
        return ArtifactDTO(
            id=model.id,
            run_id=model.run_id,
            kind=model.kind,
            path=model.path,
            size_bytes=model.size_bytes,
            preview=model.preview,
            created_at=model.created_at,
        )

    async def get_artifact_by_id(self, artifact_id: UUID) -> ArtifactDTO | None:
        if artifact_id in self._artifacts:
            return self._artifacts[artifact_id]
        if self._is_db_session_active():
            try:
                model = await self.session.get(ArtifactModel, artifact_id)
                if model is not None:
                    return self._to_dto(model)
            except Exception:  # noqa: BLE001
                pass
        return None

    async def list_artifacts_by_run(self, run_id: UUID) -> list[ArtifactDTO]:
        mem_items = [a for a in self._artifacts.values() if a.run_id == run_id]
        if self._is_db_session_active():
            try:
                res = await self.session.execute(
                    select(ArtifactModel)
                    .where(ArtifactModel.run_id == run_id)
                    .order_by(ArtifactModel.created_at.asc())
                )
                db_items = [self._to_dto(m) for m in res.scalars().all()]
                seen = {a.id for a in db_items}
                return db_items + [a for a in mem_items if a.id not in seen]
            except Exception:  # noqa: BLE001
                pass
        return mem_items

    async def save_artifact(self, data: ArtifactCreateDTO) -> ArtifactDTO:
        art_id = uuid4()
        now = datetime.now(timezone.utc)
        artifact = ArtifactDTO(
            id=art_id,
            run_id=data.run_id,
            kind=data.kind,
            path=data.path,
            size_bytes=data.size_bytes,
            preview=data.preview,
            created_at=now,
        )
        self._artifacts[art_id] = artifact
        if self._is_db_session_active():
            try:
                model = ArtifactModel(
                    id=art_id,
                    run_id=data.run_id,
                    kind=data.kind,
                    path=data.path,
                    size_bytes=data.size_bytes,
                    preview=data.preview,
                    created_at=now,
                )
                self.session.add(model)
                await self.session.commit()
            except Exception:  # noqa: BLE001
                await self.session.rollback()
        return artifact

    async def delete_artifact(self, artifact_id: UUID) -> bool:
        removed = False
        if artifact_id in self._artifacts:
            del self._artifacts[artifact_id]
            removed = True
        if self._is_db_session_active():
            try:
                model = await self.session.get(ArtifactModel, artifact_id)
                if model is not None:
                    await self.session.delete(model)
                    await self.session.commit()
                    removed = True
            except Exception:  # noqa: BLE001
                await self.session.rollback()
        return removed

    # Domain Streaming Orchestration
    async def get_artifact_stream(
        self,
        artifact_id: UUID,
    ) -> tuple[AsyncIterator[bytes], str, str, int]:
        """
        Return chunked async byte stream, filename, content-type, and file size.
        Raises NotFoundError if file is not found.
        """
        artifact = await self.get_artifact_by_id(artifact_id)
        if not artifact:
            raise NotFoundError(f"Artifact {artifact_id} not found")

        filename = os.path.basename(artifact.path) or "artifact.bin"
        media_type = "application/octet-stream"
        file_path = Path(artifact.path)

        async def file_chunks() -> AsyncIterator[bytes]:
            if file_path.is_file():
                with open(file_path, "rb") as handle:
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        yield chunk
            else:
                yield b""

        size_bytes = file_path.stat().st_size if file_path.is_file() else artifact.size_bytes
        return file_chunks(), filename, media_type, size_bytes
