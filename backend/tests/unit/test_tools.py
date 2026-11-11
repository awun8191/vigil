"""Unit tests for the nine tool harness adapters.

No test needs Docker. A fake runner records every call, so a test can prove that
argument validation happens before any runner call.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from src.integrations.repositories import SandboxExecResultDTO
from src.integrations.sandbox.contract import MAX_STREAM_BYTES
from src.modules.runs.tools import (
    APPROVAL_LEVELS,
    TOOL_COUNT,
    TOOLS,
    Tool,
    ToolContext,
    ToolResult,
    get_tool,
    invoke_tool,
    tool_definitions,
)
from src.modules.runs.tools.shell import DEFAULT_TIMEOUT_SECONDS

EXPECTED_TOOL_NAMES = {
    "read",
    "read_document",
    "view_image",
    "write",
    "edit",
    "bash",
    "deliver_file",
    "generate_document",
    "ask_user",
    "manage_tasks",
    "skill_load",
    "web_search",
    "web_fetch",
    "browser_navigate",
    "browser_extract",
}



class FakeRunner:
    """A sandbox runner stub. It records every call and serves in-memory files."""

    def __init__(
        self,
        files: dict[str, str | bytes] | None = None,
        *,
        exec_result: Any = None,
        read_error: Exception | None = None,
        exec_error: Exception | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.exec_result = exec_result
        self.read_error = read_error
        self.exec_error = exec_error
        self.calls: list[tuple[Any, ...]] = []

    async def read_file(self, container_id: str, path: str) -> bytes:
        self.calls.append(("read_file", container_id, path))
        if self.read_error is not None:
            raise self.read_error
        if path not in self.files:
            raise FileNotFoundError(f"No such file: {path}")
        value = self.files[path]
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8")

    async def write_file(self, container_id: str, path: str, content: bytes) -> None:
        self.calls.append(("write_file", container_id, path, content))
        try:
            self.files[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            self.files[path] = content

    async def execute_command(
        self, container_id: str, command: str, timeout_seconds: int = 60
    ) -> SandboxExecResultDTO:
        self.calls.append(("execute_command", container_id, command, timeout_seconds))
        if self.exec_error is not None:
            raise self.exec_error
        if self.exec_result is not None:
            return self.exec_result
        return SandboxExecResultDTO(
            exit_code=0, stdout="ok\n", stderr="", duration_ms=1.0
        )

    async def create_container(self, run_id: str, timeout_seconds: int = 900) -> Any:
        raise NotImplementedError("the fake runner does not create containers")

    async def snapshot_workspace(self, container_id: str, target_tar_path: str) -> int:
        raise NotImplementedError("the fake runner does not snapshot a workspace")

    async def restore_workspace(self, container_id: str, source_tar_path: str) -> None:
        raise NotImplementedError("the fake runner does not restore a workspace")

    async def stop_and_remove(self, container_id: str) -> None:
        raise NotImplementedError("the fake runner does not remove containers")

    async def is_healthy(self) -> bool:
        return True


def make_context(
    runner: FakeRunner,
    delivery_dir: Path,
    llm: Any | None = None,
) -> ToolContext:
    """Build a tool context for one test call."""
    return ToolContext(
        runner=runner, container_id="run-1", delivery_dir=delivery_dir, llm=llm
    )


async def call(name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """Call one tool through the registry entry point."""
    return await invoke_tool(name, arguments, context)


# --- registry ------------------------------------------------------------------


def test_registry_has_the_declared_tools() -> None:
    """The registry holds exactly the declared tools."""
    assert TOOL_COUNT == 15
    assert len(TOOLS) == 15
    assert set(TOOLS) == EXPECTED_TOOL_NAMES



def test_registry_entries_are_complete() -> None:
    """Every tool has a name, a schema, a level, and a callable handler."""
    for name, tool in TOOLS.items():
        assert isinstance(tool, Tool)
        assert tool.name == name
        assert tool.description
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema
        assert tool.level in APPROVAL_LEVELS
        assert issubclass(tool.args_model, BaseModel)
        assert callable(tool.handler)


def test_registry_levels() -> None:
    """Only ask_user is external. Every other tool is local."""
    assert get_tool("ask_user").level == "external"
    for name in EXPECTED_TOOL_NAMES - {"ask_user"}:
        assert get_tool(name).level == "local"


def test_tool_definitions_match_registry() -> None:
    """The model facing declarations hold a schema and a level for each tool."""
    definitions = tool_definitions()
    assert len(definitions) == 15

    for definition in definitions:
        assert definition["name"] in EXPECTED_TOOL_NAMES
        assert definition["parameters"]["type"] == "object"
        assert definition["level"] in APPROVAL_LEVELS


async def test_unknown_tool_returns_failure(tmp_path: Path) -> None:
    """An unknown tool name returns a failed result instead of raising."""
    context = make_context(FakeRunner(), tmp_path)

    failed = await invoke_tool("does_not_exist", {}, context)

    assert failed.ok is False
    assert "unknown tool" in (failed.error or "")


# --- schema validation ---------------------------------------------------------


async def test_tool_arg_validation(tmp_path: Path) -> None:
    """A wrong argument type fails validation before the runner is touched."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": 123}, context)

    assert result.ok is False
    assert "invalid arguments" in (result.error or "")
    assert runner.calls == []


async def test_unknown_argument_is_rejected(tmp_path: Path) -> None:
    """An unknown argument fails validation before the runner is touched."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": "notes.txt", "bogus": 1}, context)

    assert result.ok is False
    assert runner.calls == []


async def test_non_mapping_arguments_are_rejected(tmp_path: Path) -> None:
    """A list of arguments fails validation instead of raising."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await invoke_tool("read", ["notes.txt"], context)  # type: ignore[arg-type]

    assert result.ok is False
    assert runner.calls == []


async def test_result_has_contract_keys(tmp_path: Path) -> None:
    """Every result serializes to the four contract keys."""
    runner = FakeRunner({"a.txt": "hello"})
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": "a.txt"}, context)

    assert set(result.model_dump()) == {"ok", "content", "artifact_ref", "error"}


# --- files ---------------------------------------------------------------------


async def test_read_returns_file_text(tmp_path: Path) -> None:
    """read returns the decoded file content."""
    runner = FakeRunner({"notes.txt": "line one\nline two\n"})
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": "notes.txt"}, context)

    assert result.ok is True
    assert result.content == "line one\nline two\n"
    assert runner.calls == [("read_file", "run-1", "notes.txt")]


async def test_read_reports_a_missing_file(tmp_path: Path) -> None:
    """read turns a missing file into a failed result."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": "missing.txt"}, context)

    assert result.ok is False
    assert "not found" in (result.error or "")


async def test_write_sends_bytes(tmp_path: Path) -> None:
    """write sends the UTF-8 bytes of the content."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("write", {"path": "out.txt", "content": "hello"}, context)

    assert result.ok is True
    assert runner.calls == [("write_file", "run-1", "out.txt", b"hello")]
    assert runner.files["out.txt"] == "hello"


async def test_edit_replaces_one_occurrence(tmp_path: Path) -> None:
    """edit replaces the single matching string."""
    runner = FakeRunner({"app.py": "value = 1\nprint(value)\n"})
    context = make_context(runner, tmp_path)

    result = await call(
        "edit",
        {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"},
        context,
    )

    assert result.ok is True
    assert runner.files["app.py"] == "value = 2\nprint(value)\n"


async def test_edit_rejects_ambiguous(tmp_path: Path) -> None:
    """edit fails when the old string occurs twice, and it does not write."""
    runner = FakeRunner({"notes.txt": "todo\ntodo\n"})
    context = make_context(runner, tmp_path)

    result = await call(
        "edit",
        {"path": "notes.txt", "old_string": "todo", "new_string": "done"},
        context,
    )

    assert result.ok is False
    assert "2 times" in (result.error or "")
    assert "ambiguous" in (result.error or "").lower()
    assert [call[0] for call in runner.calls] == ["read_file"]
    assert runner.files["notes.txt"] == "todo\ntodo\n"


async def test_edit_rejects_missing(tmp_path: Path) -> None:
    """edit fails when the old string is absent, and it does not write."""
    runner = FakeRunner({"notes.txt": "hello\n"})
    context = make_context(runner, tmp_path)

    result = await call(
        "edit",
        {"path": "notes.txt", "old_string": "absent", "new_string": "present"},
        context,
    )

    assert result.ok is False
    assert "absent" in (result.error or "")
    assert [call[0] for call in runner.calls] == ["read_file"]
    assert runner.files["notes.txt"] == "hello\n"


# --- shell ---------------------------------------------------------------------


async def test_bash_uses_the_default_timeout(tmp_path: Path) -> None:
    """bash passes the 120 second default to the runner."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("bash", {"command": "echo hi"}, context)

    assert result.ok is True
    assert DEFAULT_TIMEOUT_SECONDS == 120
    assert runner.calls == [("execute_command", "run-1", "echo hi", 120)]


async def test_bash_passes_no_stdin(tmp_path: Path) -> None:
    """bash calls the runner with no stdin argument."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    await call("bash", {"command": "echo hi", "timeout_seconds": 5}, context)

    call_args = runner.calls[0]
    assert len(call_args) == 4
    assert "stdin" not in str(call_args)


async def test_bash_caps_output(tmp_path: Path) -> None:
    """bash cuts the combined output at 256 KB and marks the cut."""
    stdout = "x" * (MAX_STREAM_BYTES + 4096)
    runner = FakeRunner(
        exec_result=SandboxExecResultDTO(
            exit_code=0, stdout=stdout, stderr="", duration_ms=2.0
        )
    )
    context = make_context(runner, tmp_path)

    result = await call("bash", {"command": "cat big.log"}, context)

    assert result.ok is True
    assert len(str(result.content).encode("utf-8")) <= MAX_STREAM_BYTES
    assert "truncated" in str(result.content)


async def test_bash_reports_nonzero_exit(tmp_path: Path) -> None:
    """A non-zero exit is a failure that still returns the output."""
    runner = FakeRunner(
        exec_result=SandboxExecResultDTO(
            exit_code=2, stdout="", stderr="boom\n", duration_ms=3.0
        )
    )
    context = make_context(runner, tmp_path)

    result = await call("bash", {"command": "false"}, context)

    assert result.ok is False
    assert "code 2" in (result.error or "")
    assert "boom" in str(result.content)


async def test_bash_returns_the_artifact_ref(tmp_path: Path) -> None:
    """bash returns the stdout overflow artifact path."""
    artifact = SimpleNamespace(stream="stdout", path="/tmp/exec-stdout.log")
    runner = FakeRunner(exec_result=SimpleNamespace(
        exit_code=0, stdout="ok", stderr="", duration_ms=1.0, artifacts=(artifact,)
    ))
    context = make_context(runner, tmp_path)

    result = await call("bash", {"command": "cat big.log"}, context)

    assert result.artifact_ref == "/tmp/exec-stdout.log"


# --- delivery ------------------------------------------------------------------


async def test_deliver_file_copies_to_the_delivery_dir(tmp_path: Path) -> None:
    """deliver_file writes the bytes and returns the host path."""
    runner = FakeRunner({"report.csv": "a,b\n1,2\n"})
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    result = await call("deliver_file", {"path": "report.csv"}, context)

    target = delivery / "report.csv"
    assert result.ok is True
    assert result.artifact_ref == str(target)
    assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert result.content["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.content["size_bytes"] == target.stat().st_size


async def test_deliver_file_keeps_the_name_inside_the_delivery_dir(tmp_path: Path) -> None:
    """A traversal name writes only its base name inside the delivery directory."""
    runner = FakeRunner({"etc/passwd": "root:x:0:0\n"})
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    result = await call(
        "deliver_file", {"path": "etc/passwd", "name": "../../escape.txt"}, context
    )

    assert result.ok is True
    target = Path(result.artifact_ref or "")
    assert target.parent == delivery.resolve()
    assert target.name == "escape.txt"


async def test_deliver_file_reports_a_missing_file(tmp_path: Path) -> None:
    """deliver_file returns a failed result for a missing file."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path / "deliveries")

    result = await call("deliver_file", {"path": "none.txt"}, context)

    assert result.ok is False
    assert "not found" in (result.error or "")


async def test_generate_document_writes_markdown(tmp_path: Path) -> None:
    """generate_document adds the extension and renders the title."""
    runner = FakeRunner()
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    result = await call(
        "generate_document",
        {"filename": "summary", "content": "Body text.", "title": "Weekly summary"},
        context,
    )

    target = delivery / "summary.md"
    assert result.ok is True
    assert result.artifact_ref == str(target)
    assert target.read_text(encoding="utf-8").startswith("# Weekly summary")
    assert result.content["format"] == "markdown"


async def test_generate_document_escapes_the_html_title(tmp_path: Path) -> None:
    """An html document escapes the title text."""
    runner = FakeRunner()
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    result = await call(
        "generate_document",
        {
            "filename": "page.html",
            "content": "<p>hi</p>",
            "format": "html",
            "title": "<script>",
        },
        context,
    )

    assert result.ok is True
    text = (delivery / "page.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


async def test_generate_document_keeps_a_complete_html_document(tmp_path: Path) -> None:
    """A complete html document is delivered as-is instead of being wrapped again."""
    runner = FakeRunner()
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    document = "<!doctype html>\n<html lang=\"en\">\n<head><title>Poem</title></head>\n<body><p>hi</p></body>\n</html>"
    result = await call(
        "generate_document",
        {"filename": "poem.html", "content": document, "format": "html"},
        context,
    )

    assert result.ok is True
    text = (delivery / "poem.html").read_text(encoding="utf-8")
    assert text == f"{document}\n"
    assert text.count("<html") == 1
    assert text.count("<body>") == 1


async def test_generate_document_docx_and_read_document_roundtrip(tmp_path: Path) -> None:
    """generate_document writes a styled .docx that read_document extracts."""
    runner = FakeRunner()
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    gen = await call(
        "generate_document",
        {
            "filename": "report",
            "title": "Q4 Review",
            "format": "docx",
            "content": "## Highlights\n- Shipped v2\n- Zero downtime\n\n| Metric | Value |\n| --- | --- |\n| Uptime | 99.99% |\n",
        },
        context,
    )

    assert gen.ok is True
    target = delivery / "report.docx"
    assert gen.artifact_ref == str(target)
    assert target.exists()
    assert gen.content["format"] == "docx"

    runner.files["report.docx"] = target.read_bytes()
    read_res = await call("read_document", {"path": "report.docx"}, context)
    assert read_res.ok is True
    assert "Q4 Review" in str(read_res.content)
    assert "Shipped v2" in str(read_res.content)
    assert "Uptime" in str(read_res.content)
    assert "99.99%" in str(read_res.content)


async def test_generate_document_xlsx_and_read_document_roundtrip(tmp_path: Path) -> None:
    """generate_document writes an .xlsx workbook that read_document extracts with sheet markers."""
    runner = FakeRunner()
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    gen = await call(
        "generate_document",
        {
            "filename": "metrics",
            "title": "Latency",
            "format": "xlsx",
            "content": "service,p50_ms,p99_ms\napi,12,48\nworker,35,110\n",
        },
        context,
    )

    assert gen.ok is True
    target = delivery / "metrics.xlsx"
    assert gen.artifact_ref == str(target)
    assert target.exists()
    assert gen.content["format"] == "xlsx"

    runner.files["metrics.xlsx"] = target.read_bytes()
    read_res = await call("read_document", {"path": "metrics.xlsx"}, context)
    assert read_res.ok is True
    assert "[sheet: Latency]" in str(read_res.content)
    assert "service\tp50_ms\tp99_ms" in str(read_res.content)
    assert "api\t12\t48" in str(read_res.content)


async def test_read_document_extracts_pdf_pages(tmp_path: Path) -> None:
    """read_document extracts PDF text with [pN] page markers."""
    import io
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    runner = FakeRunner({"blank.pdf": buf.getvalue()})
    context = make_context(runner, tmp_path / "deliveries")

    res = await call("read_document", {"path": "blank.pdf"}, context)
    assert res.ok is True
    assert "[PDF document" in str(res.content) or "[p1]" in str(res.content)


async def test_read_document_extracts_image_metadata(tmp_path: Path) -> None:
    """read_document inspects PNG headers and returns image dimensions and size."""
    import struct

    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", 640, 480)
        + b"\x08\x06\x00\x00\x00"
    )
    runner = FakeRunner({"chart.png": png_bytes})
    context = make_context(runner, tmp_path / "deliveries")

    res = await call("read_document", {"path": "chart.png"}, context)
    assert res.ok is True
    assert "[image: chart.png]" in str(res.content)
    assert "format: PNG" in str(res.content)
    assert "dimensions: 640x480px" in str(res.content)


async def test_read_document_truncates_and_writes_overflow_artifact(tmp_path: Path) -> None:
    """read_document caps returned text at max_chars and saves full text to delivery_dir."""
    long_text = "alpha beta gamma delta\n" * 100
    runner = FakeRunner({"notes.md": long_text})
    delivery = tmp_path / "deliveries"
    context = make_context(runner, delivery)

    res = await call("read_document", {"path": "notes.md", "max_chars": 200}, context)
    assert res.ok is True
    assert res.artifact_ref is not None
    overflow_path = Path(res.artifact_ref)
    assert overflow_path.exists()
    assert overflow_path.read_text(encoding="utf-8") == long_text
    assert "truncated at 200 of" in str(res.content)


async def test_read_document_rejects_path_escape(tmp_path: Path) -> None:
    """read_document refuses paths escaping /workspace."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path / "deliveries")

    res = await call("read_document", {"path": "../../etc/passwd"}, context)
    assert res.ok is False
    assert "path escape refused" in (res.error or "")
    assert "/workspace" in (res.error or "")
    assert runner.calls == []


# --- vision --------------------------------------------------------------------


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x02\x80\x00\x00\x01\xe0"
    b"\x08\x06\x00\x00\x00"
)


class FakeVision:
    """A model client stub that records the image it is asked about."""

    def __init__(self, answer: str = "A bar chart of quarterly revenue.", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[bytes, str, str | None]] = []

    async def describe_image(
        self,
        image_bytes: bytes,
        media_type: str,
        question: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        self.calls.append((image_bytes, media_type, question))
        if self.error is not None:
            raise self.error
        return self.answer


async def test_view_image_answers_from_the_vision_model(tmp_path: Path) -> None:
    """view_image sends the image bytes and the question, then returns the answer."""
    runner = FakeRunner({"chart.png": PNG_BYTES})
    vision = FakeVision()
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call(
        "view_image",
        {"path": "chart.png", "question": "What is the trend?"},
        context,
    )

    assert res.ok is True
    assert res.content["description"] == "A bar chart of quarterly revenue."
    assert res.content["media_type"] == "image/png"
    assert res.content["question"] == "What is the trend?"
    assert vision.calls == [(PNG_BYTES, "image/png", "What is the trend?")]


async def test_view_image_uses_a_full_description_by_default(tmp_path: Path) -> None:
    """view_image passes no question when the caller omits one."""
    runner = FakeRunner({"photo.jpg": b"\xff\xd8\xff\xe0" + b"0" * 32})
    vision = FakeVision()
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call("view_image", {"path": "photo.jpg"}, context)

    assert res.ok is True
    assert res.content["media_type"] == "image/jpeg"
    assert vision.calls == [(b"\xff\xd8\xff\xe0" + b"0" * 32, "image/jpeg", None)]


async def test_view_image_rejects_a_non_image(tmp_path: Path) -> None:
    """view_image refuses a document and points at read_document."""
    runner = FakeRunner({"notes.txt": "plain text"})
    vision = FakeVision()
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call("view_image", {"path": "notes.txt"}, context)

    assert res.ok is False
    assert "not a supported image" in (res.error or "")
    assert "read_document" in (res.error or "")
    assert vision.calls == []


async def test_view_image_requires_a_vision_model(tmp_path: Path) -> None:
    """view_image fails clearly when the run has no model client."""
    runner = FakeRunner({"chart.png": PNG_BYTES})
    context = make_context(runner, tmp_path / "deliveries")

    res = await call("view_image", {"path": "chart.png"}, context)

    assert res.ok is False
    assert "no vision model" in (res.error or "")


async def test_view_image_reports_a_model_failure(tmp_path: Path) -> None:
    """A provider failure becomes a failed result, not an exception."""
    runner = FakeRunner({"chart.png": PNG_BYTES})
    vision = FakeVision(error=RuntimeError("vision is not configured"))
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call("view_image", {"path": "chart.png"}, context)

    assert res.ok is False
    assert "vision is not configured" in (res.error or "")


async def test_view_image_rejects_a_file_above_the_limit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """view_image refuses an image larger than the vision limit."""
    from src.modules.runs.tools import files as files_module

    monkeypatch.setattr(files_module, "MAX_VISION_IMAGE_BYTES", 16)
    runner = FakeRunner({"chart.png": PNG_BYTES})
    vision = FakeVision()
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call("view_image", {"path": "chart.png"}, context)

    assert res.ok is False
    assert "vision limit" in (res.error or "")
    assert vision.calls == []


async def test_view_image_rejects_path_escape(tmp_path: Path) -> None:
    """view_image refuses paths escaping /workspace."""
    runner = FakeRunner()
    vision = FakeVision()
    context = make_context(runner, tmp_path / "deliveries", llm=vision)

    res = await call("view_image", {"path": "../../etc/passwd"}, context)

    assert res.ok is False
    assert "path escape refused" in (res.error or "")
    assert runner.calls == []
    assert vision.calls == []


# --- human ---------------------------------------------------------------------


async def test_ask_user_returns_a_pending_marker(tmp_path: Path) -> None:
    """ask_user returns a pending marker and does not touch the runner."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call(
        "ask_user",
        {"question": "Ship it?", "options": ["yes", "no"]},
        context,
    )

    assert result.ok is True
    assert result.content["status"] == "pending_user_input"
    assert result.content["question"] == "Ship it?"
    assert result.content["options"] == ["yes", "no"]
    assert runner.calls == []
    assert get_tool("ask_user").level == "external"


# --- tasks ---------------------------------------------------------------------


async def test_manage_tasks_create_and_list(tmp_path: Path) -> None:
    """manage_tasks creates a task and returns the new list."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    created = await call(
        "manage_tasks", {"operation": "create", "title": "Write tests"}, context
    )

    assert created.ok is True
    assert created.content["count"] == 1
    assert created.content["tasks"][0]["id"] == "task-1"
    assert created.content["tasks"][0]["status"] == "pending"

    listed = await call(
        "manage_tasks",
        {"operation": "list", "tasks": created.content["tasks"]},
        context,
    )
    assert listed.content["count"] == 1


async def test_manage_tasks_update_and_delete(tmp_path: Path) -> None:
    """manage_tasks updates the state and deletes the task."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)
    tasks = [{"id": "task-1", "title": "Write tests", "status": "pending"}]

    updated = await call(
        "manage_tasks",
        {
            "operation": "update",
            "tasks": tasks,
            "task_id": "task-1",
            "status": "completed",
        },
        context,
    )

    assert updated.ok is True
    assert updated.content["tasks"][0]["status"] == "completed"

    deleted = await call(
        "manage_tasks",
        {
            "operation": "delete",
            "tasks": updated.content["tasks"],
            "task_id": "task-1",
        },
        context,
    )

    assert deleted.ok is True
    assert deleted.content["tasks"] == []


async def test_manage_tasks_rejects_an_unknown_id(tmp_path: Path) -> None:
    """manage_tasks fails for an unknown task id."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call(
        "manage_tasks", {"operation": "delete", "task_id": "nope"}, context
    )

    assert result.ok is False
    assert "task not found" in (result.error or "")


async def test_manage_tasks_requires_the_operation_fields(tmp_path: Path) -> None:
    """manage_tasks rejects a create without a title before the handler runs."""
    runner = FakeRunner()
    context = make_context(runner, tmp_path)

    result = await call("manage_tasks", {"operation": "create"}, context)

    assert result.ok is False
    assert "invalid arguments" in (result.error or "")


# --- boundary ------------------------------------------------------------------


async def test_tool_never_raises(tmp_path: Path) -> None:
    """A runner error becomes a failed result, not an exception."""
    runner = FakeRunner(read_error=RuntimeError("daemon down"))
    context = make_context(runner, tmp_path)

    result = await call("read", {"path": "a.txt"}, context)

    assert result.ok is False
    assert "RuntimeError" in (result.error or "")
    assert "daemon down" in (result.error or "")


# --- external routing -----------------------------------------------------------


class RecordingMCPHost:
    """A connector host stub. It records every call and never runs a tool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def list_tools(self, connector_id: str) -> list[Any]:
        return []

    async def execute_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str = "",
    ) -> Any:
        from src.integrations.repositories import MCPToolResultDTO

        self.calls.append((connector_id, tool_name, dict(arguments)))
        return MCPToolResultDTO(
            tool_call_id=tool_call_id, output="host ran it", is_error=False
        )

    async def get_tool_approval_policy(self, connector_id: str, tool_name: str) -> str:
        return "external"

    async def is_healthy(self, connector_id: str) -> bool:
        return True


class NoSandbox:
    """A sandbox stub that fails the test if a container is ever asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_container(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("create_container")
        raise AssertionError("ask_user must not need a sandbox container")


def make_executor(
    tmp_path: Path,
    host: Any,
    sandbox: Any | None = None,
) -> Any:
    """Build a RunToolExecutor over the recording stubs."""
    from uuid import uuid4

    from src.config.settings import Settings
    from src.dependencies import RunToolExecutor

    return RunToolExecutor(
        session=None,
        settings=Settings(APP_ENV="test"),
        run_id=uuid4(),
        sandbox=sandbox if sandbox is not None else NoSandbox(),
        mcp_host=host,
        delivery_dir=tmp_path,
    )


async def test_execute_external_runs_ask_user_through_the_harness(tmp_path: Path) -> None:
    """ask_user is a declared external tool: its own handler answers, not the host."""
    host = RecordingMCPHost()
    sandbox = NoSandbox()
    executor = make_executor(tmp_path, host, sandbox)

    result = await executor.execute_external("ask_user", {"question": "hi"})

    assert result["ok"] is True
    assert result["error"] is None
    assert result["content"]["status"] == "pending_user_input"
    assert result["content"]["question"] == "hi"
    assert host.calls == []
    assert sandbox.calls == []
    assert executor.container_id is None


async def test_execute_external_validates_a_declared_tool_before_it_runs(
    tmp_path: Path,
) -> None:
    """A bad ask_user call fails on its schema and still never reaches the host."""
    host = RecordingMCPHost()
    executor = make_executor(tmp_path, host)

    result = await executor.execute_external("ask_user", {})

    assert result["ok"] is False
    assert "invalid arguments" in str(result["error"])
    assert host.calls == []


async def test_execute_external_sends_an_undeclared_tool_to_the_host(
    tmp_path: Path,
) -> None:
    """A name the harness does not declare is a third party tool: the host runs it."""
    host = RecordingMCPHost()
    executor = make_executor(tmp_path, host)

    result = await executor.execute_external("third_party_lookup", {"q": "x"})

    assert host.calls == [("default", "third_party_lookup", {"q": "x"})]
    assert result["ok"] is True
    assert result["content"] == "host ran it"


async def test_stub_mcp_host_fails_closed() -> None:
    """The import-failure fallback reports an error, never an invented success."""
    from src.dependencies import _StubMCPHost

    result = await _StubMCPHost().execute_tool(
        "default", "echo_ext", {"msg": "hi"}, tool_call_id="call-stub-1"
    )

    assert result.is_error is True
    assert result.tool_call_id == "call-stub-1"
    assert "unavailable" in str(result.output)
    assert "mock" not in str(result.output)
