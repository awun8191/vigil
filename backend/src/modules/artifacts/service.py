"""Artifacts service implementing sandbox file registration and binary streaming."""
from __future__ import annotations

import asyncio
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

ARTIFACT_MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".diff": "text/plain; charset=utf-8",
    ".patch": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def resolve_media_type(filename: str) -> str:
    """Resolve the HTTP Content-Type for an artifact filename."""
    ext = Path(filename).suffix.lower()
    return ARTIFACT_MEDIA_TYPES.get(ext, "application/octet-stream")


class ArtifactsService:
    """
    Artifacts management service satisfying ArtifactsRepository via duck typing.
    Constructor injection of session and settings.

    A database-backed service reports database failures to the caller. The
    process-local dict is the store only for a sessionless service (tests, the
    CLI harness); a read on a live session that fails raises instead of
    answering stale memory as if it were the row.

    A write on a live session flushes and returns. The transaction boundary is
    the caller's: the request session, the worker and the test own the commit.
    """

    _artifacts: ClassVar[dict[UUID, ArtifactDTO]] = {}

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    @property
    def _is_db_session_active(self) -> bool:
        return self.session is not None

    def _require_session(self) -> AsyncSession:
        """Return the request session; the caller has already proved it is present."""
        assert self.session is not None
        return self.session

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
        if not self._is_db_session_active:
            return self._artifacts.get(artifact_id)
        model = await self._require_session().get(ArtifactModel, artifact_id)
        if model is None:
            return None
        return self._to_dto(model)

    async def list_artifacts_by_run(self, run_id: UUID) -> list[ArtifactDTO]:
        if not self._is_db_session_active:
            return [a for a in self._artifacts.values() if a.run_id == run_id]
        res = await self._require_session().execute(
            select(ArtifactModel)
            .where(ArtifactModel.run_id == run_id)
            .order_by(ArtifactModel.created_at.asc())
        )
        return [self._to_dto(m) for m in res.scalars().all()]

    async def save_artifact(self, data: ArtifactCreateDTO) -> ArtifactDTO:
        now = datetime.now(timezone.utc)
        if not self._is_db_session_active:
            artifact = ArtifactDTO(
                id=uuid4(),
                run_id=data.run_id,
                kind=data.kind,
                path=data.path,
                size_bytes=data.size_bytes,
                preview=data.preview,
                created_at=now,
            )
            self._artifacts[artifact.id] = artifact
            return artifact
        model = ArtifactModel(
            id=uuid4(),
            run_id=data.run_id,
            kind=data.kind,
            path=data.path,
            size_bytes=data.size_bytes,
            preview=data.preview,
            created_at=now,
        )
        session = self._require_session()
        session.add(model)
        await session.flush()
        return self._to_dto(model)

    async def delete_artifact(self, artifact_id: UUID) -> bool:
        if not self._is_db_session_active:
            return self._artifacts.pop(artifact_id, None) is not None
        session = self._require_session()
        model = await session.get(ArtifactModel, artifact_id)
        if model is None:
            return False
        await session.delete(model)
        await session.flush()
        return True

    # Domain Streaming Orchestration
    async def get_artifact_stream(
        self,
        artifact_id: UUID,
    ) -> tuple[AsyncIterator[bytes], str, str, int]:
        """
        Return chunked async byte stream, filename, content-type, and file size.
        Raises NotFoundError if file is not found.

        The file read runs in a worker thread: the artifact body is on disk, and
        a blocking read inside the generator would stall the event loop for the
        whole transfer. Chunk size stays 64 KiB, so the stream semantics are
        unchanged.
        """
        artifact = await self.get_artifact_by_id(artifact_id)
        if not artifact:
            raise NotFoundError(f"Artifact {artifact_id} not found")

        filename = os.path.basename(artifact.path) or "artifact.bin"
        media_type = resolve_media_type(filename)
        file_path = Path(artifact.path)
        is_file = await asyncio.to_thread(file_path.is_file)
        size_bytes = (
            await asyncio.to_thread(lambda: file_path.stat().st_size)
            if is_file
            else artifact.size_bytes
        )

        async def file_chunks() -> AsyncIterator[bytes]:
            if not is_file:
                yield b""
                return
            handle = await asyncio.to_thread(open, file_path, "rb")
            try:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(handle.close)

        return file_chunks(), filename, media_type, size_bytes

    async def get_artifact_text(
        self,
        artifact_id: UUID,
        max_chars: int = 400_000,
    ) -> str:
        """Return the artifact body as text for the document viewers.

        PDF, DOCX and XLSX files are extracted with the same readers the
        ``read_document`` tool uses. Text files are decoded directly. The read
        runs in a worker thread so a large document never blocks the loop.
        """
        artifact = await self.get_artifact_by_id(artifact_id)
        if not artifact:
            raise NotFoundError(f"Artifact {artifact_id} not found")

        file_path = Path(artifact.path)
        if not await asyncio.to_thread(file_path.is_file):
            raise NotFoundError(f"Artifact file for {artifact_id} is missing")

        def read_text() -> str:
            suffix = file_path.suffix.lower()
            if suffix in {".pdf", ".docx", ".xlsx"}:
                from src.modules.runs.tools.files import (
                    _extract_docx_text,
                    _extract_pdf_text,
                    _extract_xlsx_text,
                )

                data = file_path.read_bytes()
                if suffix == ".pdf":
                    return _extract_pdf_text(data)
                if suffix == ".docx":
                    return _extract_docx_text(data)
                return _extract_xlsx_text(data)
            return file_path.read_text(encoding="utf-8", errors="replace")

        text = await asyncio.to_thread(read_text)
        return text[:max_chars]
