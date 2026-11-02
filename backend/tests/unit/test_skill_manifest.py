"""Unit tests for the skill manifest, the path guard, and the commit pin.

No test needs Postgres, Docker, or the network. The install tests stop at the
validation step, before any Git command runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.exceptions import NotFoundError, ValidationError
from src.config.settings import Settings
from src.modules.skills.service import (
    SkillManifest,
    SkillsService,
    collect_skill_files,
    parse_frontmatter,
    parse_manifest,
    resolve_skill_dir,
    validate_commit_pin,
    validate_repo_url,
    validate_skill_name,
)

VALID_MANIFEST = """---
name: web-search
description: Search the web and return the top results.
version: 1.2.0
---

Use this skill to search.
"""


def test_manifest_parses_required_fields() -> None:
    """A manifest with all three fields becomes a SkillManifest."""
    manifest = parse_manifest(VALID_MANIFEST)

    assert manifest == SkillManifest(
        name="web-search",
        description="Search the web and return the top results.",
        version="1.2.0",
    )


def test_manifest_requires_version() -> None:
    """A manifest without a version field is rejected."""
    text = """---
name: web-search
description: Search the web.
---

Body text.
"""

    with pytest.raises(ValidationError) as caught:
        parse_manifest(text)

    assert "version" in caught.value.message


def test_manifest_requires_name_and_description() -> None:
    """A manifest without a name or a description is rejected."""
    text = """---
version: 0.1.0
---
"""
    with pytest.raises(ValidationError) as caught:
        parse_manifest(text)

    assert "name" in caught.value.message
    assert "description" in caught.value.message


def test_manifest_requires_frontmatter() -> None:
    """A file without a frontmatter block is rejected."""
    with pytest.raises(ValidationError):
        parse_manifest("# web-search\n\nNo frontmatter here.\n")


def test_manifest_rejects_unclosed_frontmatter() -> None:
    """A frontmatter block without a closing marker is rejected."""
    with pytest.raises(ValidationError):
        parse_frontmatter("---\nname: web-search\n")


def test_manifest_rejects_a_name_with_a_slash() -> None:
    """The manifest name is one path segment."""
    text = """---
name: ../etc
description: Escape attempt.
version: 1.0.0
---
"""
    with pytest.raises(ValidationError):
        parse_manifest(text)


def test_skill_path_traversal_rejected(tmp_path: Path) -> None:
    """A skill name of ../../etc is rejected and never leaves the root."""
    root = tmp_path / "skills"
    root.mkdir()

    with pytest.raises(ValidationError):
        resolve_skill_dir(root, "../../etc")

    with pytest.raises(ValidationError):
        resolve_skill_dir(root, "../etc")

    with pytest.raises(ValidationError):
        resolve_skill_dir(root, "/etc")


def test_skill_name_accepts_one_safe_segment(tmp_path: Path) -> None:
    """A lowercase name with dots and hyphens resolves below the root."""
    root = tmp_path / "skills"
    root.mkdir()

    target = resolve_skill_dir(root, "web-search.v2")

    assert target == (root / "web-search.v2").resolve()


def test_skill_name_rejects_a_dot_dot_segment() -> None:
    """A name that holds .. is rejected even without a slash."""
    with pytest.raises(ValidationError):
        validate_skill_name("..")
    with pytest.raises(ValidationError):
        validate_skill_name("a..b")


@pytest.mark.asyncio
async def test_install_requires_sha(tmp_path: Path) -> None:
    """An install with a branch name instead of a SHA is rejected."""
    settings = Settings(APP_ENV="test", SKILLS_ROOT=str(tmp_path / "skills"))
    service = SkillsService(session=None, settings=settings)

    with pytest.raises(ValidationError) as caught:
        await service.install_from_git(
            "https://example.invalid/web-search.git", "main"
        )

    assert "commit SHA" in caught.value.message

    with pytest.raises(ValidationError):
        await service.install_from_git("https://example.invalid/web-search.git", None)

    with pytest.raises(ValidationError):
        await service.install_from_git(
            "https://example.invalid/web-search.git", "v1.2.0"
        )

    # Nothing was cloned and nothing was registered.
    assert await service.list_skills() == []
    assert not (tmp_path / "skills").exists()


def test_commit_pin_accepts_a_full_sha() -> None:
    """A 40-character hexadecimal SHA is a valid pin."""
    sha = "0123456789abcdef0123456789abcdef01234567"

    assert validate_commit_pin(sha) == sha
    assert validate_commit_pin(sha.upper()) == sha


def test_repo_url_rejects_a_local_path_and_an_option() -> None:
    """A local path, a file URL, and a leading option are rejected."""
    for value in ("/tmp/repo", "file:///tmp/repo", "--upload-pack=/bin/sh", ""):
        with pytest.raises(ValidationError):
            validate_repo_url(value)

    assert validate_repo_url("https://github.com/example/skill.git").startswith(
        "https://"
    )


def test_collect_skill_files_rejects_an_escape(tmp_path: Path) -> None:
    """A symlink inside a skill is rejected instead of followed."""
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(VALID_MANIFEST, encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "escape").symlink_to("/etc/passwd")

    with pytest.raises(ValidationError) as caught:
        collect_skill_files(skill_dir)

    assert "symlink" in caught.value.message


def test_collect_skill_files_requires_a_manifest(tmp_path: Path) -> None:
    """A directory without SKILL.md is not a skill."""
    skill_dir = tmp_path / "empty"
    skill_dir.mkdir()

    with pytest.raises(ValidationError):
        collect_skill_files(skill_dir)


def test_collect_skill_files_rejects_a_missing_directory(tmp_path: Path) -> None:
    """A missing skill directory is a not-found error."""
    with pytest.raises(NotFoundError):
        collect_skill_files(tmp_path / "absent")
