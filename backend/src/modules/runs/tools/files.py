"""File tools: ``read``, ``read_document``, ``write`` and ``edit`` inside the sandbox workspace.

Each handler calls only the sandbox runner protocol. The runner resolves the path below
``/workspace`` and rejects a path outside it. The handlers hold no state between calls.
"""
from __future__ import annotations

import io
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.integrations.sandbox.contract import container_path
from src.modules.runs.tools import Tool, ToolContext, ToolResult

DEFAULT_MAX_DOCUMENT_CHARS = 20_000
MAX_SHEET_ROWS = 500
MAX_VISION_IMAGE_BYTES = 10 * 1024 * 1024


class ReadArgs(BaseModel):
    """Arguments for the ``read`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to read. The path resolves inside /workspace.",
    )


class ReadDocumentArgs(BaseModel):
    """Arguments for the ``read_document`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="Document to read (.pdf, .docx, .xlsx, .md, .txt, .csv). The path resolves inside /workspace.",
    )
    max_chars: int = Field(
        default=DEFAULT_MAX_DOCUMENT_CHARS,
        ge=100,
        le=200_000,
        description="Maximum characters to return inline. Full extracted text is saved to an overflow file when truncated.",
    )


class ViewImageArgs(BaseModel):
    """Arguments for the ``view_image`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="Image to view (.png, .jpg, .jpeg, .webp, .gif). The path resolves inside /workspace.",
    )
    question: str | None = Field(
        default=None,
        max_length=2000,
        description="What to answer about the image. The default is a full description.",
    )


class WriteArgs(BaseModel):
    """Arguments for the ``write`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to write. The path resolves inside /workspace.",
    )
    content: str = Field(..., description="Full UTF-8 text of the file.")


class EditArgs(BaseModel):
    """Arguments for the ``edit`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File to edit. The path resolves inside /workspace.",
    )
    old_string: str = Field(
        ...,
        min_length=1,
        description="Exact text to replace. It must occur exactly once.",
    )
    new_string: str = Field(..., description="Replacement text.")


def _decode(payload: bytes) -> str:
    """Decode file bytes without raising on invalid UTF-8."""
    return payload.decode("utf-8", errors="replace")


async def read(args: ReadArgs, context: ToolContext) -> ToolResult:
    """Read one file and return its text."""
    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")
    if isinstance(payload, str):
        return ToolResult.success(payload)
    return ToolResult.success(_decode(payload))


def _detect_document_format(path: str, data: bytes) -> str:
    """Sniff document or image format from file extension and magic bytes."""
    ext = Path(path).suffix.lower()
    if data.startswith(b"%PDF") or ext == ".pdf":
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n") or ext == ".png":
        return "image"
    if data.startswith(b"\xff\xd8\xff") or ext in (".jpg", ".jpeg"):
        return "image"
    if data.startswith((b"GIF87a", b"GIF89a")) or ext == ".gif":
        return "image"
    if (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP") or ext == ".webp":
        return "image"
    if data.startswith(b"BM") and ext == ".bmp":
        return "image"
    if ext == ".svg":
        return "svg"
    if ext == ".docx":
        return "docx"
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if data.startswith(b"PK\x03\x04"):
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("word/") for n in names):
                    return "docx"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
        except Exception:  # noqa: BLE001
            pass
    return "text"


def _image_media_type(data: bytes) -> str | None:
    """Return the vision MIME type of an image from its magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _extract_image_metadata(path: str, data: bytes) -> str:
    """Extract dimensions, format, and size from a binary image without external C libraries."""
    import struct

    size_bytes = len(data)
    ext = Path(path).suffix.lower().lstrip(".") or "image"
    width: int | None = None
    height: int | None = None
    fmt = ext.upper()

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        fmt = "PNG"
        width, height = struct.unpack(">II", data[16:24])
    elif data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        fmt = "GIF"
        width, height = struct.unpack("<HH", data[6:10])
    elif len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        fmt = "WEBP"
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            width, height = w & 0x3FFF, h & 0x3FFF
        elif chunk == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0xF) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
    elif data.startswith(b"\xff\xd8\xff"):
        fmt = "JPEG"
        idx = 2
        while idx + 9 < len(data):
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[idx + 5 : idx + 9])
                break
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            seg_len = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
            if seg_len < 2:
                break
            idx += 2 + seg_len

    dim_str = f"{width}x{height}px" if width and height else "unknown dimensions"
    return (
        f"[image: {Path(path).name}]\n"
        f"format: {fmt}\n"
        f"dimensions: {dim_str}\n"
        f"size_bytes: {size_bytes}\n"
        "Use view_image to see the image content, or deliver_file to send it to the chat interface."
    )


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF byte stream with [pN] page markers."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("install pypdf/openpyxl/python-docx to read PDF documents") from exc

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append(f"[p{idx}]\n{text}" if text else f"[p{idx}]")
    return "\n\n".join(pages)


def _extract_docx_text(data: bytes) -> str:
    """Extract paragraphs and tables from a DOCX byte stream."""
    try:
        import docx
    except ImportError as exc:
        raise RuntimeError("install pypdf/openpyxl/python-docx to read DOCX documents") from exc

    doc = docx.Document(io.BytesIO(data))
    blocks: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)
    for table in doc.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def _extract_xlsx_text(data: bytes) -> str:
    """Extract sheet rows from an XLSX byte stream with row caps."""
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("install pypdf/openpyxl/python-docx to read XLSX documents") from exc

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets: list[str] = []
        for name in wb.sheetnames:
            ws = wb[name]
            lines: list[str] = [f"[sheet: {name}]"]
            count = 0
            for row in ws.iter_rows(values_only=True):
                if all(v is None for v in row):
                    continue
                count += 1
                if count > MAX_SHEET_ROWS:
                    lines.append(f"... [truncated after {MAX_SHEET_ROWS} rows]")
                    break
                lines.append("\t".join("" if v is None else str(v) for v in row))
            sheets.append("\n".join(lines))
        return "\n\n".join(sheets)
    finally:
        wb.close()


async def read_document(args: ReadDocumentArgs, context: ToolContext) -> ToolResult:
    """Read and extract structured text from a document (.pdf, .docx, .xlsx, image, or plain text)."""
    try:
        container_path(args.path)
    except ValueError as exc:
        return ToolResult.failure(f"path escape refused: {args.path}: {exc}")

    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")

    raw_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    doc_format = _detect_document_format(args.path, raw_bytes)

    try:
        if doc_format == "pdf":
            extracted = _extract_pdf_text(raw_bytes)
        elif doc_format == "docx":
            extracted = _extract_docx_text(raw_bytes)
        elif doc_format == "xlsx":
            extracted = _extract_xlsx_text(raw_bytes)
        elif doc_format == "image":
            extracted = _extract_image_metadata(args.path, raw_bytes)
        else:
            extracted = payload if isinstance(payload, str) else _decode(raw_bytes)
    except RuntimeError as exc:
        return ToolResult.failure(str(exc))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"failed to extract {doc_format} from {args.path}: {exc}")

    artifact_ref: str | None = None
    if len(extracted) > args.max_chars:
        context.delivery_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(args.path).stem or "document"
        overflow_path = (context.delivery_dir / f"{stem}.extracted.txt").resolve()
        if overflow_path.parent == context.delivery_dir.resolve():
            overflow_path.write_text(extracted, encoding="utf-8")
            artifact_ref = str(overflow_path)
        truncated_text = (
            f"{extracted[: args.max_chars]}\n\n"
            f"... [truncated at {args.max_chars} of {len(extracted)} chars]"
        )
        return ToolResult.success(truncated_text, artifact_ref=artifact_ref)

    return ToolResult.success(extracted, artifact_ref=None)


async def view_image(args: ViewImageArgs, context: ToolContext) -> ToolResult:
    """Answer a question about one image through the run's vision model."""
    try:
        container_path(args.path)
    except ValueError as exc:
        return ToolResult.failure(f"path escape refused: {args.path}: {exc}")

    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")

    raw_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    media_type = _image_media_type(raw_bytes)
    if media_type is None:
        return ToolResult.failure(
            f"{args.path} is not a supported image. Use view_image for png, jpeg, "
            "webp or gif, and read_document for documents."
        )
    if not raw_bytes:
        return ToolResult.failure(f"{args.path} is empty")
    if len(raw_bytes) > MAX_VISION_IMAGE_BYTES:
        return ToolResult.failure(
            f"{args.path} is {len(raw_bytes)} bytes; the vision limit is "
            f"{MAX_VISION_IMAGE_BYTES} bytes"
        )

    describe = getattr(context.llm, "describe_image", None)
    if not callable(describe):
        return ToolResult.failure(
            "no vision model is available in this run; set VISION_LLM_API_KEY "
            "or FAST_LLM_API_KEY"
        )

    try:
        description = await describe(raw_bytes, media_type, args.question)
    except Exception as exc:  # noqa: BLE001 - the tool boundary must not raise
        return ToolResult.failure(f"failed to view {args.path}: {exc}")

    if not isinstance(description, str) or not description.strip():
        return ToolResult.failure(
            f"the vision model returned no answer for {args.path}"
        )

    content: dict[str, object] = {
        "path": args.path,
        "media_type": media_type,
        "description": description.strip(),
    }
    if args.question:
        content["question"] = args.question
    return ToolResult.success(content)


async def write(args: WriteArgs, context: ToolContext) -> ToolResult:
    """Write one file with the full supplied text."""
    data = args.content.encode("utf-8")
    await context.runner.write_file(context.container_id, args.path, data)
    return ToolResult.success(f"wrote {len(data)} bytes to {args.path}")


async def edit(args: EditArgs, context: ToolContext) -> ToolResult:
    """Replace one unique string in a file.

    The tool fails when the old string is absent or when it occurs more than once. It
    never guesses a location. A failed edit does not write the file.
    """
    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")
    original = payload if isinstance(payload, str) else _decode(payload)

    occurrences = original.count(args.old_string)
    if occurrences == 0:
        return ToolResult.failure(
            f"old_string is absent from {args.path}. No edit was applied."
        )
    if occurrences > 1:
        return ToolResult.failure(
            f"old_string appears {occurrences} times in {args.path}. "
            "The edit is ambiguous. Pass a longer old_string that occurs once."
        )

    updated = original.replace(args.old_string, args.new_string, 1)
    await context.runner.write_file(
        context.container_id, args.path, updated.encode("utf-8")
    )
    return ToolResult.success(f"edited {args.path}: replaced 1 occurrence")


FILE_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="read",
        description="Read a plain text file from the workspace.",
        level="local",
        args_model=ReadArgs,
        handler=read,
    ),
    Tool.define(
        name="read_document",
        description=(
            "Extract text or metadata from a document or image (.pdf, .docx, .xlsx, .png, .jpg, .webp, .gif, .svg, .md, .txt, .csv) in the workspace. "
            "Use this instead of read for PDF, Word, Excel, and image files."
        ),
        level="local",
        args_model=ReadDocumentArgs,
        handler=read_document,
    ),
    Tool.define(
        name="view_image",
        description=(
            "View an image (.png, .jpg, .jpeg, .webp, .gif) with a vision model and answer a "
            "question about its content. Use this to read screenshots, photos, charts, and scans. "
            "Use read_document for PDF, Word, and Excel files."
        ),
        level="local",
        args_model=ViewImageArgs,
        handler=view_image,
    ),
    Tool.define(
        name="write",
        description="Write a full text file to the workspace.",
        level="local",
        args_model=WriteArgs,
        handler=write,
    ),
    Tool.define(
        name="edit",
        description=(
            "Replace one unique string in a workspace file. "
            "The tool fails when the string is absent or ambiguous."
        ),
        level="local",
        args_model=EditArgs,
        handler=edit,
    ),
)


__all__ = [
    "DEFAULT_MAX_DOCUMENT_CHARS",
    "FILE_TOOLS",
    "MAX_VISION_IMAGE_BYTES",
    "EditArgs",
    "ReadArgs",
    "ReadDocumentArgs",
    "ViewImageArgs",
    "WriteArgs",
    "edit",
    "read",
    "read_document",
    "view_image",
    "write",
]
