"""Delivery tools: ``deliver_file`` and ``generate_document``.

Both tools read from the workspace or render text, then write one file to the delivery
directory on the host. They return the host path in ``artifact_ref``. They hold no state.
"""
from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.modules.runs.tools import Tool, ToolContext, ToolResult

DocumentFormat = Literal["markdown", "text", "html", "csv"]

#: File extension for each document format.
FORMAT_EXTENSIONS: dict[str, str] = {
    "markdown": ".md",
    "text": ".txt",
    "html": ".html",
    "csv": ".csv",
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
        description="Document format: markdown, text, html or csv.",
    )
    title: str | None = Field(
        default=None,
        max_length=500,
        description="Optional title. The tool renders it for markdown and text.",
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


def _render(args: GenerateDocumentArgs) -> str:
    """Render the document body for the requested format."""
    if args.format == "html":
        title = escape(args.title or Path(args.filename).stem)
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
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
    data = _render(args).encode("utf-8")
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
            "Render a markdown, text, html or csv document and deliver it to the user."
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
