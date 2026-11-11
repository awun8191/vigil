"""The artifacts service reports database failures and streams off the loop.

A service built on a request session must not answer from the process-local
dict when the query fails: a database error is the caller's problem, and stale
memory returned as a row is a silent lie. Only a sessionless service (tests,
the CLI harness) owns the in-memory store.

The stream also reads the file in a worker thread. The assertions below pin the
observable contract: the same 64 KiB chunking, the real file size, and the empty
chunk for a missing file.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.config.settings import Settings
from src.modules.artifacts.repository import ArtifactCreateDTO
from src.modules.artifacts.service import ArtifactsService

CHUNK_BYTES = 65536


class _BrokenSession:
    """A session whose every database call fails, like a dropped connection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, *args: object, **kwargs: object) -> object:
        self.calls.append("get")
        raise RuntimeError("database is gone")

    async def execute(self, *args: object, **kwargs: object) -> object:
        self.calls.append("execute")
        raise RuntimeError("database is gone")

    def add(self, *args: object, **kwargs: object) -> None:
        self.calls.append("add")
        raise RuntimeError("database is gone")

    async def commit(self) -> None:
        self.calls.append("commit")
        raise RuntimeError("database is gone")

    async def flush(self) -> None:
        self.calls.append("flush")
        raise RuntimeError("database is gone")

    async def delete(self, *args: object, **kwargs: object) -> None:
        self.calls.append("delete")
        raise RuntimeError("database is gone")


def _settings() -> Settings:
    return Settings(APP_ENV="test")


@pytest.mark.asyncio
async def test_broken_session_raises_instead_of_returning_none() -> None:
    """A failing read raises; it never degrades to None or to stale memory."""
    session = _BrokenSession()
    service = ArtifactsService(session=session, settings=_settings())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="database is gone"):
        await service.get_artifact_by_id(uuid4())

    with pytest.raises(RuntimeError, match="database is gone"):
        await service.list_artifacts_by_run(uuid4())

    assert session.calls, "the service must have tried the database"


@pytest.mark.asyncio
async def test_broken_session_write_raises_instead_of_reporting_success() -> None:
    """A failing write raises; a save is not a success because it happened in RAM."""
    session = _BrokenSession()
    service = ArtifactsService(session=session, settings=_settings())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="database is gone"):
        await service.save_artifact(
            ArtifactCreateDTO(
                run_id=uuid4(),
                kind="file",
                path="/tmp/never-written",
                size_bytes=0,
                preview=None,
            )
        )

    with pytest.raises(RuntimeError, match="database is gone"):
        await service.delete_artifact(uuid4())


@pytest.mark.asyncio
async def test_sessionless_service_keeps_the_memory_store() -> None:
    """Without a session the service is the test double, and the dict is the store."""
    service = ArtifactsService(session=None, settings=_settings())
    run_id = uuid4()

    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=run_id,
            kind="file",
            path="/tmp/memory-only.txt",
            size_bytes=12,
            preview="hello",
        )
    )

    fetched = await service.get_artifact_by_id(saved.id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert [a.id for a in await service.list_artifacts_by_run(run_id)] == [saved.id]
    assert await service.delete_artifact(saved.id) is True
    assert await service.get_artifact_by_id(saved.id) is None


@pytest.mark.asyncio
async def test_stream_reads_the_file_in_64k_chunks(tmp_path: Path) -> None:
    """The stream still yields 64 KiB chunks and the real size, in order."""
    payload = (b"".join(bytes([index % 251]) * 1024 for index in range(200)))[: 2 * CHUNK_BYTES + 512]
    target = tmp_path / "artifact.bin"
    target.write_bytes(payload)

    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="file",
            path=str(target),
            size_bytes=0,
            preview=None,
        )
    )

    chunks, filename, media_type, size_bytes = await service.get_artifact_stream(saved.id)
    collected = [chunk async for chunk in chunks]

    assert filename == "artifact.bin"
    assert media_type == "application/octet-stream"
    assert size_bytes == len(payload)
    assert [len(chunk) for chunk in collected] == [CHUNK_BYTES, CHUNK_BYTES, 512]
    assert b"".join(collected) == payload


@pytest.mark.asyncio
async def test_stream_of_a_missing_file_yields_one_empty_chunk(tmp_path: Path) -> None:
    """A row whose file is gone still answers with the recorded size and no bytes."""
    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="file",
            path=str(tmp_path / "gone.bin"),
            size_bytes=99,
            preview=None,
        )
    )

    chunks, _, _, size_bytes = await service.get_artifact_stream(saved.id)
    assert [chunk async for chunk in chunks] == [b""]
    assert size_bytes == 99


@pytest.mark.asyncio
async def test_stream_of_an_unknown_artifact_is_not_found() -> None:
    """An id with no row raises instead of streaming nothing."""
    from src.config.exceptions import NotFoundError

    service = ArtifactsService(session=None, settings=_settings())
    with pytest.raises(NotFoundError):
        await service.get_artifact_stream(UUID(int=0))


@pytest.mark.asyncio
async def test_text_reads_a_plain_text_artifact(tmp_path: Path) -> None:
    """A text artifact is decoded directly, without extraction."""
    target = tmp_path / "notes.md"
    target.write_text("# Notes\n\nOne line.", encoding="utf-8")

    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="document",
            path=str(target),
            size_bytes=0,
            preview=None,
        )
    )

    assert await service.get_artifact_text(saved.id) == "# Notes\n\nOne line."


@pytest.mark.asyncio
async def test_text_extracts_a_docx_artifact(tmp_path: Path) -> None:
    """A DOCX artifact is read through the document extractor."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Quarterly review", level=1)
    document.add_paragraph("Revenue grew.")
    target = tmp_path / "review.docx"
    document.save(target)

    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="document",
            path=str(target),
            size_bytes=target.stat().st_size,
            preview=None,
        )
    )

    text = await service.get_artifact_text(saved.id)
    assert "Quarterly review" in text
    assert "Revenue grew." in text


@pytest.mark.asyncio
async def test_text_caps_the_extracted_body(tmp_path: Path) -> None:
    """The caller's character cap applies to the extracted text."""
    target = tmp_path / "long.txt"
    target.write_text("abcdefghij", encoding="utf-8")

    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="document",
            path=str(target),
            size_bytes=10,
            preview=None,
        )
    )

    assert await service.get_artifact_text(saved.id, max_chars=4) == "abcd"


@pytest.mark.asyncio
async def test_text_of_a_missing_file_is_not_found(tmp_path: Path) -> None:
    """A row whose file is gone answers NotFound instead of empty text."""
    from src.config.exceptions import NotFoundError

    service = ArtifactsService(session=None, settings=_settings())
    saved = await service.save_artifact(
        ArtifactCreateDTO(
            run_id=uuid4(),
            kind="document",
            path=str(tmp_path / "gone.md"),
            size_bytes=10,
            preview=None,
        )
    )

    with pytest.raises(NotFoundError):
        await service.get_artifact_text(saved.id)


@pytest.mark.asyncio
async def test_stream_resolves_media_type_by_extension(tmp_path: Path) -> None:
    """Known document and image extensions resolve to their specific MIME types."""
    service = ArtifactsService(session=None, settings=_settings())
    cases = [
        ("report.pdf", "application/pdf"),
        ("memo.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("notes.md", "text/markdown; charset=utf-8"),
        ("table.csv", "text/csv; charset=utf-8"),
        ("chart.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("preview.webp", "image/webp"),
        ("diagram.svg", "image/svg+xml"),
        ("raw.bin", "application/octet-stream"),
    ]
    for filename, expected_mime in cases:
        target = tmp_path / filename
        target.write_bytes(b"data")
        saved = await service.save_artifact(
            ArtifactCreateDTO(
                run_id=uuid4(),
                kind="file",
                path=str(target),
                size_bytes=4,
                preview=None,
            )
        )
        _, actual_name, media_type, _ = await service.get_artifact_stream(saved.id)
        assert actual_name == filename
        assert media_type == expected_mime
