"""Delivery tools: ``deliver_file`` and ``generate_document``.

Both tools read from the workspace or render text, then write one file to the delivery
directory on the host. They return the host path in ``artifact_ref``. They hold no state.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from html import escape
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.modules.runs.tools import Tool, ToolContext, ToolResult

DocumentFormat = Literal["markdown", "text", "html", "csv", "docx", "xlsx"]

#: File extension for each document format.
FORMAT_EXTENSIONS: dict[str, str] = {
    "markdown": ".md",
    "text": ".txt",
    "html": ".html",
    "csv": ".csv",
    "docx": ".docx",
    "xlsx": ".xlsx",
}


class DeliverFileArgs(BaseModel):
    """Arguments for the ``deliver_file`` tool."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="Workspace file to deliver to the user.",
    )
    name: str | None = Field(
        default=None,
        max_length=255,
        description="Host file name. The default is the base name of path.",
    )


class GenerateDocumentArgs(BaseModel):
    """Arguments for the ``generate_document`` tool."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Document name. The tool adds the extension for the format.",
    )
    content: str = Field(..., description="Document body.")
    format: DocumentFormat = Field(
        default="markdown",
        description="Document format: markdown, text, html, csv, docx or xlsx.",
    )
    title: str | None = Field(
        default=None,
        max_length=500,
        description="Optional title. The tool renders it for markdown, text, docx and xlsx.",
    )


def _leaf(name: str) -> str:
    """Return a safe base file name. Directory components are removed."""
    if "\x00" in name:
        raise ValueError("a file name must not contain a NUL byte")
    leaf = Path(name.replace("\\", "/")).name
    if leaf in ("", ".", ".."):
        raise ValueError(f"invalid file name: {name!r}")
    return leaf


def _write(directory: Path, name: str, payload: bytes) -> Path:
    """Write bytes to the delivery directory and return the host path."""
    directory.mkdir(parents=True, exist_ok=True)
    root = directory.resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError(f"delivery path escapes the delivery directory: {name!r}")
    target.write_bytes(payload)
    return target


def _summary(target: Path, payload: bytes) -> dict[str, object]:
    """Return the artifact metadata for a delivered file."""
    return {
        "path": str(target),
        "name": target.name,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


#: Matches an HTML document that already declares its own root element.
_FULL_HTML_DOCUMENT = re.compile(r"(?is)^\s*(?:<!doctype\s+html|<html[\s>])")


def _render(args: GenerateDocumentArgs) -> str:
    """Render the document body for the requested text format."""
    if args.format == "html":
        body = args.content.strip()
        if _FULL_HTML_DOCUMENT.search(body):
            return f"{body}\n"
        title = escape(args.title or Path(args.filename).stem)
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{title}</title>\n"
            "</head>\n"
            "<body>\n"
            f"{args.content}\n"
            "</body>\n"
            "</html>\n"
        )
    if args.format == "markdown" and args.title:
        return f"# {args.title}\n\n{args.content}\n"
    if args.format == "text" and args.title:
        return f"{args.title}\n\n{args.content}\n"
    if args.format == "csv":
        return args.content if args.content.endswith("\n") else f"{args.content}\n"
    return f"{args.content}\n"


def _parse_markdown_table(lines: list[str]) -> list[list[str]]:
    """Parse contiguous Markdown pipe-table lines into cell rows."""
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue
        rows.append(cells)
    return rows


def _render_docx_bytes(args: GenerateDocumentArgs) -> bytes:
    """Build a styled .docx binary payload with heading, normal, and table styles."""
    try:
        import docx
    except ImportError as exc:
        raise RuntimeError("install pypdf/openpyxl/python-docx to generate DOCX documents") from exc

    doc = docx.Document()
    if args.title:
        doc.add_heading(args.title, level=0)

    lines = args.content.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            idx += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines: list[str] = []
            while idx < len(lines) and lines[idx].strip().startswith("|") and lines[idx].strip().endswith("|"):
                table_lines.append(lines[idx])
                idx += 1
            rows = _parse_markdown_table(table_lines)
            if rows:
                cols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=cols)
                table.style = "Table Grid"
                for r_idx, row_vals in enumerate(rows):
                    for c_idx, val in enumerate(row_vals):
                        cell = table.cell(r_idx, c_idx)
                        cell.text = val
                        if r_idx == 0 and cell.paragraphs and cell.paragraphs[0].runs:
                            for run in cell.paragraphs[0].runs:
                                run.bold = True
            continue

        if stripped.startswith("### "):
            doc.add_heading(stripped[4:].strip(), level=3)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:].strip(), level=2)
        elif stripped.startswith("# "):
            doc.add_heading(stripped[2:].strip(), level=1)
        elif stripped.startswith(("- ", "* ")):
            doc.add_paragraph(stripped[2:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(stripped, style="Normal")
        idx += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _parse_tabular_content(content: str) -> list[list[Any]]:
    """Parse JSON rows, Markdown tables, or CSV/TSV text into a 2D list."""
    stripped = content.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list) and parsed:
                if isinstance(parsed[0], dict):
                    headers = list(parsed[0].keys())
                    rows: list[list[Any]] = [headers]
                    for item in parsed:
                        if isinstance(item, dict):
                            rows.append([item.get(h, "") for h in headers])
                    return rows
                if isinstance(parsed[0], list):
                    return [list(r) for r in parsed if isinstance(r, list)]
        except Exception:  # noqa: BLE001
            pass

    lines = stripped.splitlines()
    if any(line.strip().startswith("|") and line.strip().endswith("|") for line in lines):
        md_rows = _parse_markdown_table(lines)
        if md_rows:
            return md_rows

    delimiter = "\t" if "\t" in lines[0] and "," not in lines[0] else ","
    reader = csv.reader(io.StringIO(stripped), delimiter=delimiter)
    return [row for row in reader if any(cell.strip() for cell in row)]


def _render_xlsx_bytes(args: GenerateDocumentArgs) -> bytes:
    """Build a styled .xlsx binary payload with bold headers, freeze panes, auto-filter, and column widths."""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("install pypdf/openpyxl/python-docx to generate XLSX documents") from exc

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    sheet_title = (args.title or Path(args.filename).stem or "Sheet1")[:31]
    for bad in ("\\", "/", "*", "[", "]", ":", "?"):
        sheet_title = sheet_title.replace(bad, "-")
    ws.title = sheet_title or "Sheet1"

    rows = _parse_tabular_content(args.content)
    if not rows:
        rows = [["Content"], [args.content]]

    for row in rows:
        converted: list[Any] = []
        for val in row:
            if isinstance(val, (int, float)):
                converted.append(val)
            elif isinstance(val, str):
                s = val.strip()
                try:
                    converted.append(int(s))
                except ValueError:
                    try:
                        converted.append(float(s))
                    except ValueError:
                        converted.append(val)
            else:
                converted.append(str(val) if val is not None else "")
        ws.append(converted)

    header_font = Font(bold=True, color="1F2937")
    header_fill = PatternFill(fill_type="solid", start_color="F3F4F6", end_color="F3F4F6")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")

    ws.freeze_panes = "A2"
    if ws.max_row >= 1 and ws.max_column >= 1:
        last_col = get_column_letter(ws.max_column)
        ws.auto_filter.ref = f"A1:{last_col}{ws.max_row}"

    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for row_idx in range(1, ws.max_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            text = str(val) if val is not None else ""
            if len(text) > max_len:
                max_len = len(text)
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 50)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_bytes(args: GenerateDocumentArgs) -> bytes:
    """Render the document payload as bytes for any supported format."""
    if args.format == "docx":
        return _render_docx_bytes(args)
    if args.format == "xlsx":
        return _render_xlsx_bytes(args)
    return _render(args).encode("utf-8")


async def deliver_file(args: DeliverFileArgs, context: ToolContext) -> ToolResult:
    """Copy one workspace file to the delivery directory."""
    try:
        payload = await context.runner.read_file(context.container_id, args.path)
    except FileNotFoundError as exc:
        return ToolResult.failure(f"file not found: {args.path}: {exc}")
    data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    name = _leaf(args.name or args.path)
    target = _write(context.delivery_dir, name, data)
    return ToolResult.success(_summary(target, data), artifact_ref=str(target))


async def generate_document(
    args: GenerateDocumentArgs, context: ToolContext
) -> ToolResult:
    """Render a document and write it to the delivery directory."""
    name = _leaf(args.filename)
    extension = FORMAT_EXTENSIONS[args.format]
    if not name.lower().endswith(extension):
        name = f"{name}{extension}"
    try:
        data = _render_bytes(args)
    except RuntimeError as exc:
        return ToolResult.failure(str(exc))
    target = _write(context.delivery_dir, name, data)
    summary = _summary(target, data)
    summary["format"] = args.format
    return ToolResult.success(summary, artifact_ref=str(target))


DELIVERY_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="deliver_file",
        description="Copy a workspace file to the delivery directory for the user.",
        level="local",
        args_model=DeliverFileArgs,
        handler=deliver_file,
    ),
    Tool.define(
        name="generate_document",
        description=(
            "Render a markdown, text, html, csv, docx or xlsx document and deliver it to the user."
        ),
        level="local",
        args_model=GenerateDocumentArgs,
        handler=generate_document,
    ),
)


__all__ = [
    "DELIVERY_TOOLS",
    "FORMAT_EXTENSIONS",
    "DeliverFileArgs",
    "DocumentFormat",
    "GenerateDocumentArgs",
    "deliver_file",
    "generate_document",
]
