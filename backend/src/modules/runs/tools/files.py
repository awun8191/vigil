"""File tools: ``read``, ``read_document``, ``view_image``, ``write`` and ``edit`` inside the sandbox workspace.

Each handler calls the sandbox runner protocol (with host workspace fallback when a host
workspace directory is attached to the run). The runner resolves the path below
``/workspace`` and rejects a path outside it. The handlers hold no state between calls.
"""
from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.integrations.sandbox.contract import WORKSPACE_PATH, container_path
from src.modules.runs.tools import Tool, ToolContext, ToolResult

DEFAULT_MAX_DOCUMENT_CHARS = 20_000
MAX_SHEET_ROWS = 500
MAX_VISION_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_PREVIEW_FILES = 15
MAX_ARCHIVE_FILE_PREVIEW_CHARS = 2_000

_HOST_WORKSPACE_MARKER_RE = re.compile(
    r"^/+.*?(?:workspaces/(?:projects|sessions|uploads|runs)/[^/]+|workspace)(?=/|$)(.*)$"
)


def normalize_tool_workspace_path(path: str, workspace_dir: Path | None = None) -> str:
    """Normalize a host workspace path prefix to a container-relative or /workspace path.

    Genuine path escapes (such as ``../../etc/passwd`` or ``/etc/passwd``) are preserved
    verbatim so ``container_path`` rejects them.
    """
    if not isinstance(path, str) or not path.strip():
        return path
    stripped = path.strip()
    if workspace_dir is not None:
        ws_str = str(workspace_dir.resolve()).rstrip("/")
        if ws_str and (stripped == ws_str or stripped.startswith(ws_str + "/")):
            suffix = stripped[len(ws_str) :].lstrip("/")
            return f"{WORKSPACE_PATH}/{suffix}" if suffix else WORKSPACE_PATH
    if stripped.startswith("/") and not (
        stripped == WORKSPACE_PATH or stripped.startswith(WORKSPACE_PATH + "/")
    ):
        if ".." not in Path(stripped).parts:
            match = _HOST_WORKSPACE_MARKER_RE.match(stripped)
            if match:
                rest = (match.group(1) or "").lstrip("/")
                return f"{WORKSPACE_PATH}/{rest}" if rest else WORKSPACE_PATH
    return path


def _resolve_host_workspace_file(path: str, workspace_dir: Path | None) -> Path | None:
    """Resolve a validated /workspace path against the host workspace directory."""
    if workspace_dir is None:
        return None
    try:
        cpath = container_path(path)
    except ValueError:
        return None
    rel = posixpath.relpath(cpath, WORKSPACE_PATH)
    if rel in (".", "") or rel.startswith(".."):
        return None
    root = workspace_dir.resolve()
    candidate = (root / rel).resolve()
    if root not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    # Also check uploads/ subdirectory when the user references /workspace/<name> or vice versa
    leaf = Path(rel).name
    if leaf:
        alt_uploads = (root / "uploads" / leaf).resolve()
        if root in alt_uploads.parents and alt_uploads.is_file():
            return alt_uploads
        alt_root = (root / leaf).resolve()
        if alt_root.parent == root and alt_root.is_file():
            return alt_root
    return None


async def read_workspace_bytes(path: str, context: ToolContext) -> bytes | str:
    """Read file bytes from the sandbox runner, falling back to the host workspace directory."""
    normalized = normalize_tool_workspace_path(path, context.workspace_dir)
    first_exc: FileNotFoundError | None = None
    try:
        payload = await context.runner.read_file(context.container_id, normalized)
        # _StubSandboxRunner returns b"" unconditionally; if a real file exists in workspace_dir, prefer it
        if payload == b"" and context.workspace_dir is not None:
            host_file = _resolve_host_workspace_file(normalized, context.workspace_dir)
            if host_file is not None and host_file.stat().st_size > 0:
                return host_file.read_bytes()
        return payload
    except FileNotFoundError as exc:
        first_exc = exc

    # Try alternate /workspace/uploads/<name> or /workspace/<name> inside the runner only when a host workspace is present
    if context.workspace_dir is not None:
        host_file = _resolve_host_workspace_file(normalized, context.workspace_dir)
        if host_file is not None:
            return host_file.read_bytes()

    assert first_exc is not None
    raise first_exc


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
        description=(
            "Document to read (.pdf, .docx, .xlsx, .pptx, .csv, .tsv, .json, .yaml, .xml, "
            ".html, .md, .txt, .zip, .tar.gz). The path resolves inside /workspace."
        ),
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


def _detect_document_format(path: str, data: bytes) -> str:
    """Sniff document, archive, or image format from file extension and magic bytes."""
    lower_name = Path(path).name.lower()
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
    if ext == ".pptx":
        return "pptx"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("word/") for n in names):
                    return "docx"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return "pptx"
                return "archive"
        except Exception:  # noqa: BLE001
            pass
    if (
        ext in (".zip", ".tar", ".tgz", ".gz")
        or lower_name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))
        or data.startswith(b"\x1f\x8b")
    ):
        return "archive"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext == ".json":
        return "json"
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


def _extract_pptx_text(data: bytes) -> str:
    """Extract slide text, tables, and speaker notes from a PPTX byte stream."""
    slide_re = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
    notes_re = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")

    def _xml_paragraphs(xml_bytes: bytes) -> list[str]:
        root = ET.fromstring(xml_bytes)
        paragraphs: list[str] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "p":
                runs = [
                    (node.text or "")
                    for node in elem.iter()
                    if (node.tag.split("}")[-1] if "}" in node.tag else node.tag) == "t"
                    and node.text
                ]
                line = "".join(runs).strip()
                if line:
                    paragraphs.append(line)
        return paragraphs

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slide_entries: list[tuple[int, str]] = []
        notes_by_num: dict[int, str] = {}
        for name in zf.namelist():
            s_match = slide_re.match(name)
            if s_match:
                slide_entries.append((int(s_match.group(1)), name))
                continue
            n_match = notes_re.match(name)
            if n_match:
                notes_by_num[int(n_match.group(1))] = name

        slide_entries.sort(key=lambda item: item[0])
        slides_out: list[str] = []
        for num, slide_name in slide_entries:
            lines = [f"[slide {num}]"]
            paras = _xml_paragraphs(zf.read(slide_name))
            if paras:
                lines.extend(paras)
            note_name = notes_by_num.get(num)
            if note_name:
                note_paras = _xml_paragraphs(zf.read(note_name))
                if note_paras:
                    lines.append(f"[notes: {' / '.join(note_paras)}]")
            slides_out.append("\n".join(lines))
        return "\n\n".join(slides_out) if slides_out else "[empty presentation]"


def _extract_csv_text(path: str, data: bytes) -> str:
    """Extract structured summary and rows from a CSV or TSV file."""
    text = _decode(data).strip()
    if not text:
        return f"[csv: {Path(path).name} | empty]"
    lines = text.splitlines()
    delimiter = "\t" if Path(path).suffix.lower() == ".tsv" or ("\t" in lines[0] and "," not in lines[0]) else ","
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    non_empty = [row for row in reader if any(cell.strip() for cell in row)]
    if not non_empty:
        return f"[csv: {Path(path).name} | empty]"
    headers = non_empty[0]
    data_rows = non_empty[1:]
    summary = (
        f"[csv: {Path(path).name} | {len(data_rows)} rows x {len(headers)} columns | "
        f"columns: {', '.join(h or f'col_{i+1}' for i, h in enumerate(headers[:20]))}]"
    )
    out_lines = [summary, "\t".join(headers)]
    for idx, row in enumerate(data_rows, start=1):
        if idx > MAX_SHEET_ROWS:
            out_lines.append(f"... [truncated after {MAX_SHEET_ROWS} rows]")
            break
        out_lines.append("\t".join(row))
    return "\n".join(out_lines)


def _extract_json_text(path: str, data: bytes) -> str:
    """Parse and format JSON with a structural header."""
    raw = _decode(data).strip()
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    if isinstance(parsed, dict):
        keys = list(parsed.keys())
        header = f"[json: {Path(path).name} | object with {len(keys)} keys: {', '.join(str(k) for k in keys[:20])}]"
    elif isinstance(parsed, list):
        header = f"[json: {Path(path).name} | array with {len(parsed)} items]"
    else:
        header = f"[json: {Path(path).name}]"
    formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
    return f"{header}\n{formatted}"


def _extract_archive_text(path: str, data: bytes) -> str:
    """List archive members and preview small text files inside .zip or .tar(.gz) archives."""
    name = Path(path).name
    entries: list[str] = []
    previews: list[str] = []

    if data.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            for info in infos[:200]:
                entries.append(f"- {info.filename} ({info.file_size} B)")
            for info in infos[:MAX_ARCHIVE_PREVIEW_FILES]:
                ext = Path(info.filename).suffix.lower()
                if info.file_size <= 64_000 and ext in (
                    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml",
                    ".py", ".ts", ".tsx", ".js", ".html", ".css", ".toml", ".xml", ".sql", ".sh",
                ):
                    snippet = _decode(zf.read(info.filename))[:MAX_ARCHIVE_FILE_PREVIEW_CHARS].strip()
                    if snippet:
                        previews.append(f"--- {info.filename} ---\n{snippet}")
        total = len(entries)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            for m in members[:200]:
                entries.append(f"- {m.name} ({m.size} B)")
            for m in members[:MAX_ARCHIVE_PREVIEW_FILES]:
                ext = Path(m.name).suffix.lower()
                if m.size <= 64_000 and ext in (
                    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml",
                    ".py", ".ts", ".tsx", ".js", ".html", ".css", ".toml", ".xml", ".sql", ".sh",
                ):
                    extracted_f = tf.extractfile(m)
                    if extracted_f is not None:
                        snippet = _decode(extracted_f.read())[:MAX_ARCHIVE_FILE_PREVIEW_CHARS].strip()
                        if snippet:
                            previews.append(f"--- {m.name} ---\n{snippet}")
        total = len(entries)

    header = f"[archive: {name} | {total} files]"
    sections = [header, "\n".join(entries) if entries else "(empty archive)"]
    if previews:
        sections.append("\n\n".join(previews))
    return "\n\n".join(sections)


async def read(args: ReadArgs, context: ToolContext) -> ToolResult:
    """Read one file and return its text (automatically extracting binary documents if passed)."""
    normalized_path = normalize_tool_workspace_path(args.path, context.workspace_dir)
    try:
        payload = await read_workspace_bytes(normalized_path, context)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")

    if isinstance(payload, bytes):
        doc_format = _detect_document_format(normalized_path, payload)
        if doc_format in ("pdf", "docx", "xlsx", "pptx", "image", "archive"):
            try:
                if doc_format == "pdf":
                    return ToolResult.success(_extract_pdf_text(payload))
                if doc_format == "docx":
                    return ToolResult.success(_extract_docx_text(payload))
                if doc_format == "xlsx":
                    return ToolResult.success(_extract_xlsx_text(payload))
                if doc_format == "pptx":
                    return ToolResult.success(_extract_pptx_text(payload))
                if doc_format == "image":
                    return ToolResult.success(_extract_image_metadata(normalized_path, payload))
                if doc_format == "archive":
                    return ToolResult.success(_extract_archive_text(normalized_path, payload))
            except Exception as exc:  # noqa: BLE001
                return ToolResult.failure(f"failed to extract {doc_format} from {args.path}: {exc}")
        return ToolResult.success(_decode(payload))
    return ToolResult.success(payload)


async def read_document(args: ReadDocumentArgs, context: ToolContext) -> ToolResult:
    """Read and extract structured text from a document (.pdf, .docx, .xlsx, .pptx, .csv, .json, archive, image, or plain text)."""
    normalized_path = normalize_tool_workspace_path(args.path, context.workspace_dir)
    try:
        container_path(normalized_path)
    except ValueError as exc:
        return ToolResult.failure(f"path escape refused: {args.path}: {exc}")

    try:
        payload = await read_workspace_bytes(normalized_path, context)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")

    raw_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    doc_format = _detect_document_format(normalized_path, raw_bytes)

    try:
        if doc_format == "pdf":
            extracted = _extract_pdf_text(raw_bytes)
        elif doc_format == "docx":
            extracted = _extract_docx_text(raw_bytes)
        elif doc_format == "xlsx":
            extracted = _extract_xlsx_text(raw_bytes)
        elif doc_format == "pptx":
            extracted = _extract_pptx_text(raw_bytes)
        elif doc_format == "csv":
            extracted = _extract_csv_text(normalized_path, raw_bytes)
        elif doc_format == "json":
            extracted = _extract_json_text(normalized_path, raw_bytes)
        elif doc_format == "archive":
            extracted = _extract_archive_text(normalized_path, raw_bytes)
        elif doc_format == "image":
            extracted = _extract_image_metadata(normalized_path, raw_bytes)
        else:
            extracted = payload if isinstance(payload, str) else _decode(raw_bytes)
    except RuntimeError as exc:
        return ToolResult.failure(str(exc))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"failed to extract {doc_format} from {args.path}: {exc}")

    artifact_ref: str | None = None
    if len(extracted) > args.max_chars:
        context.delivery_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(normalized_path).stem or "document"
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
    normalized_path = normalize_tool_workspace_path(args.path, context.workspace_dir)
    try:
        container_path(normalized_path)
    except ValueError as exc:
        return ToolResult.failure(f"path escape refused: {args.path}: {exc}")

    try:
        payload = await read_workspace_bytes(normalized_path, context)
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
    normalized_path = normalize_tool_workspace_path(args.path, context.workspace_dir)
    data = args.content.encode("utf-8")
    await context.runner.write_file(context.container_id, normalized_path, data)
    if context.workspace_dir is not None:
        try:
            cpath = container_path(normalized_path)
            rel = posixpath.relpath(cpath, WORKSPACE_PATH)
            if rel not in (".", "") and not rel.startswith(".."):
                root = context.workspace_dir.resolve()
                target = (root / rel).resolve()
                if root in target.parents:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
        except Exception:  # noqa: BLE001
            pass
    return ToolResult.success(f"wrote {len(data)} bytes to {args.path}")


async def edit(args: EditArgs, context: ToolContext) -> ToolResult:
    """Replace one unique string in a file.

    The tool fails when the old string is absent or when it occurs more than once. It
    never guesses a location. A failed edit does not write the file.
    """
    normalized_path = normalize_tool_workspace_path(args.path, context.workspace_dir)
    try:
        payload = await read_workspace_bytes(normalized_path, context)
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
    updated_bytes = updated.encode("utf-8")
    await context.runner.write_file(
        context.container_id, normalized_path, updated_bytes
    )
    if context.workspace_dir is not None:
        try:
            cpath = container_path(normalized_path)
            rel = posixpath.relpath(cpath, WORKSPACE_PATH)
            if rel not in (".", "") and not rel.startswith(".."):
                root = context.workspace_dir.resolve()
                target = (root / rel).resolve()
                if root in target.parents:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(updated_bytes)
        except Exception:  # noqa: BLE001
            pass
    return ToolResult.success(f"edited {args.path}: replaced 1 occurrence")


FILE_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="read",
        description="Read a file from the workspace (plain text or auto-extracted document/archive).",
        level="local",
        args_model=ReadArgs,
        handler=read,
    ),
    Tool.define(
        name="read_document",
        description=(
            "Extract text, tables, slides, or metadata from a document, archive, or image "
            "(.pdf, .docx, .xlsx, .pptx, .csv, .tsv, .json, .zip, .tar.gz, .png, .jpg, .webp, .gif, .svg, .md, .txt) "
            "in the workspace. Use this for PDF, Word, Excel, PowerPoint, CSV, JSON, archives, and image files."
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
            "Use read_document for PDF, Word, Excel, and PowerPoint files."
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
    "normalize_tool_workspace_path",
    "read",
    "read_document",
    "read_workspace_bytes",
    "view_image",
    "write",
]
