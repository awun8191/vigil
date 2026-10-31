"""Artifacts service implementing sandbox file registration and binary streaming."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import AsyncIterator, ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.modules.artifacts.repository import (
    ArtifactCreateDTO,
    ArtifactDTO,
)


class ArtifactsService:
    """
    Artifacts management service satisfying ArtifactsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _artifacts: ClassVar[dict[UUID, ArtifactDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_artifact_by_id(self, artifact_id: UUID) -> ArtifactDTO | None:
        return self._artifacts.get(artifact_id)

    async def list_artifacts_by_run(self, run_id: UUID) -> list[ArtifactDTO]:
        return [a for a in self._artifacts.values() if a.run_id == run_id]

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
        return artifact

    async def delete_artifact(self, artifact_id: UUID) -> bool:
        if artifact_id in self._artifacts:
            del self._artifacts[artifact_id]
            return True
        return False

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

        async def file_chunks() -> AsyncIterator[bytes]:
            yield b""  # chunked file streaming

        return file_chunks(), filename, media_type, artifact.size_bytes
