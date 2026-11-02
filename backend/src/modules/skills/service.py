"""Skills service: manifest parsing, pinned Git installs, and sandbox loading.

The registry lives in PostgreSQL. The service reads and writes the ``skills``
table through the injected session, so an installed skill survives a restart and
a second service instance sees it. A sessionless service keeps a private,
in-memory store for a unit test. Such an instance never shares state, and the
application always passes a session.

An install from Git must pin a commit SHA. A branch name or a tag moves, so the
service rejects it before it starts a clone. The clone is extracted to a
directory below the configured skills root. ``storage_path`` never points at a
Git working tree.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError, ValidationError
from src.config.settings import Settings
from src.integrations.repositories import SandboxRunnerProtocol
from src.modules.skills.repository import SkillCreateDTO, SkillDTO
from src.schemas.skills import SkillModel

#: The manifest file at the top of a skill directory.
MANIFEST_FILENAME = "SKILL.md"

#: Every field that the frontmatter must hold.
MANIFEST_REQUIRED_FIELDS: tuple[str, ...] = ("name", "description", "version")

#: A skill name is one path segment. It starts with a letter or a digit and
#: holds only lowercase letters, digits, dots, hyphens, and underscores.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: A commit pin is a hexadecimal SHA. Branches and tags do not match.
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

#: An install URL is https or a scp-like ssh address. No local path and no
#: leading option.
REPO_URL_PATTERN = re.compile(r"^(https?://[^\s]+|git@[A-Za-z0-9._-]+:[^\s]+)$")

#: A skill copy stays small. A larger directory is not a skill.
MAX_SKILL_FILES = 512
MAX_SKILL_BYTES = 10 * 1024 * 1024

#: Git runs without a prompt and without a pager.
GIT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class SkillManifest:
    """The three fields of a skill manifest."""

    name: str
    description: str
    version: str


def validate_skill_name(name: str) -> str:
    """Return the name when it is one safe path segment.

    Raises:
        ValidationError: The name is empty, holds a slash, or holds ``..``.
    """
    value = name.strip() if isinstance(name, str) else ""
    if not value or not SKILL_NAME_PATTERN.fullmatch(value):
        raise ValidationError(
            f"invalid skill name: {name!r}. A name is one path segment of "
            "lowercase letters, digits, dots, hyphens, or underscores."
        )
    if ".." in value:
        raise ValidationError(
            f"invalid skill name: {name!r}. The name must not hold '..'."
        )
    return value


def resolve_skill_dir(root: str | Path, name: str) -> Path:
    """Resolve ``name`` below ``root`` and refuse to escape the root.

    The name is validated first. The resolved path is checked against the
    resolved root a second time, so a symbolic link cannot escape either.
    """
    safe_name = validate_skill_name(name)
    base = Path(root).expanduser().resolve()
    target = (base / safe_name).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValidationError(f"skill path escapes the skills root: {name!r}")
    return target


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the YAML frontmatter of a Markdown file.

    The file must start with a ``---`` line and hold a closing ``---`` line.
    """
    if not isinstance(text, str):
        raise ValidationError("the manifest must be text")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValidationError("the manifest must start with a '---' frontmatter line")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        raise ValidationError("the manifest frontmatter has no closing '---' line")
    block = "\n".join(lines[1:closing])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"the manifest frontmatter is not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValidationError("the manifest frontmatter must be a YAML mapping")
    return data


def parse_manifest(text: str) -> SkillManifest:
    """Parse a manifest and require ``name``, ``description``, and ``version``.

    Raises:
        ValidationError: A field is absent, empty, or not a scalar.
    """
    data = parse_frontmatter(text)
    missing: list[str] = []
    values: dict[str, str] = {}
    for field in MANIFEST_REQUIRED_FIELDS:
        value = data.get(field)
        if isinstance(value, bool) or isinstance(value, (dict, list)) or value is None:
            missing.append(field)
            continue
        text_value = str(value).strip()
        if not text_value:
            missing.append(field)
            continue
        values[field] = text_value
    if missing:
        raise ValidationError(
            "the manifest is missing a required field: " + ", ".join(missing)
        )
    return SkillManifest(
        name=validate_skill_name(values["name"]),
        description=values["description"],
        version=values["version"],
    )


def read_manifest(skill_dir: str | Path) -> SkillManifest:
    """Read and parse ``SKILL.md`` in a skill directory."""
    path = Path(skill_dir) / MANIFEST_FILENAME
    if not path.is_file():
        raise ValidationError(f"the skill has no {MANIFEST_FILENAME} manifest")
    return parse_manifest(path.read_text(encoding="utf-8"))


def collect_skill_files(skill_dir: str | Path) -> dict[str, bytes]:
    """Read every regular file below a skill directory.

    The key is the POSIX path relative to the directory. A symbolic link is
    rejected, because it can point outside the skill. The ``.git`` directory of
    a clone is skipped.
    """
    base = Path(skill_dir).resolve()
    if not base.is_dir():
        raise NotFoundError(f"skill directory not found: {base}")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValidationError(f"a skill file must not be a symlink: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        if relative.startswith(".git/"):
            continue
        total += path.stat().st_size
        if len(files) >= MAX_SKILL_FILES or total > MAX_SKILL_BYTES:
            raise ValidationError("the skill directory is too large to copy")
        files[relative] = path.read_bytes()
    if MANIFEST_FILENAME not in files:
        raise ValidationError(f"the skill has no {MANIFEST_FILENAME} manifest")
    return files


def validate_commit_pin(commit: str) -> str:
    """Return a lowercase commit SHA. A branch name or a tag is rejected."""
    value = commit.strip().lower() if isinstance(commit, str) else ""
    if not COMMIT_SHA_PATTERN.fullmatch(value):
        raise ValidationError(
            "an install requires a commit SHA. A branch name or a tag moves, "
            "so it is not a pin."
        )
    return value


def validate_repo_url(repo_url: str) -> str:
    """Return a Git URL that the installer accepts.

    The value must be https, http, or a scp-like ssh address. A local path, a
    leading option, or whitespace is rejected.
    """
    value = repo_url.strip() if isinstance(repo_url, str) else ""
    if not value or value.startswith("-") or not REPO_URL_PATTERN.fullmatch(value):
        raise ValidationError(
            f"invalid skill repository URL: {repo_url!r}. Use an https or ssh Git URL."
        )
    return value


def _run_git(arguments: list[str], cwd: Path | None = None) -> str:
    """Run one Git command without a shell and return its standard output."""
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        env=environment,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:400]
        raise ValidationError(f"git {arguments[0]} failed: {detail}")
    return completed.stdout.strip()


class SkillsService:
    """Skill registry service that satisfies SkillsRepository via duck typing.

    The constructor injects the session and the settings. With a session the
    registry is the PostgreSQL ``skills`` table. Without a session the service
    keeps a private in-memory store, which a unit test uses.
    """

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self._memory: dict[UUID, SkillDTO] = {}

    # --- registry reads -------------------------------------------------------

    async def get_by_name(self, name: str) -> SkillDTO | None:
        """Return the skill with this name, or None."""
        if self.session is None:
            for skill in self._memory.values():
                if skill.name == name:
                    return skill
            return None
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        row = result.scalars().first()
        return self._to_dto(row) if row is not None else None

    async def get_by_id(self, skill_id: UUID) -> SkillDTO | None:
        """Return the skill with this id, or None."""
        if self.session is None:
            return self._memory.get(skill_id)
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        row = result.scalars().first()
        return self._to_dto(row) if row is not None else None

    async def list_skills(self, enabled_only: bool = False) -> list[SkillDTO]:
        """List the registry in name order."""
        if self.session is None:
            skills = sorted(self._memory.values(), key=lambda item: item.name)
            if enabled_only:
                skills = [skill for skill in skills if skill.enabled]
            return skills
        statement = select(SkillModel).order_by(SkillModel.name)
        if enabled_only:
            statement = statement.where(SkillModel.enabled.is_(True))
        result = await self.session.execute(statement)
        return [self._to_dto(row) for row in result.scalars().all()]

    # --- registry writes ------------------------------------------------------

    async def save_skill(self, data: SkillCreateDTO) -> SkillDTO:
        """Insert the skill, or update the row with the same name.

        A reinstall of the same name replaces the description, version, source,
        commit, storage path, and enabled flag. It never creates a second row.
        """
        name = validate_skill_name(data.name)
        now = datetime.now(timezone.utc)
        if self.session is None:
            existing = await self.get_by_name(name)
            skill = SkillDTO(
                id=existing.id if existing is not None else uuid4(),
                name=name,
                description=data.description,
                version=str(data.version),
                source=data.source,
                commit=data.commit,
                enabled=data.enabled,
                storage_path=data.storage_path,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            self._memory[skill.id] = skill
            return skill

        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        row = result.scalars().first()
        if row is None:
            row = SkillModel(
                id=uuid4(),
                name=name,
                description=data.description,
                version=str(data.version),
                source=data.source,
                commit=data.commit,
                enabled=data.enabled,
                storage_path=data.storage_path,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.description = data.description
            row.version = str(data.version)
            row.source = data.source
            row.commit = data.commit
            row.enabled = data.enabled
            row.storage_path = data.storage_path
            row.updated_at = now
        await self.session.commit()
        return self._to_dto(row)

    async def update_status(self, skill_id: UUID, enabled: bool) -> SkillDTO | None:
        """Set the enabled flag. It never deletes the row."""
        now = datetime.now(timezone.utc)
        if self.session is None:
            skill = self._memory.get(skill_id)
            if skill is None:
                return None
            updated = SkillDTO(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                version=skill.version,
                source=skill.source,
                commit=skill.commit,
                enabled=enabled,
                storage_path=skill.storage_path,
                created_at=skill.created_at,
                updated_at=now,
            )
            self._memory[skill_id] = updated
            return updated

        result = await self.session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        row = result.scalars().first()
        if row is None:
            return None
        row.enabled = enabled
        row.updated_at = now
        await self.session.commit()
        return self._to_dto(row)

    async def delete_skill(self, skill_id: UUID) -> bool:
        """Remove one registry row. The API never calls this method."""
        if self.session is None:
            return self._memory.pop(skill_id, None) is not None
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        row = result.scalars().first()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    # --- domain orchestration -------------------------------------------------

    async def install_from_git(
        self, repo_url: str, commit: str | None = None
    ) -> SkillDTO:
        """Clone a pinned commit, extract it below the skills root, and register it.

        The commit pin is validated first. A branch name is rejected before any
        network call. The clone goes to a staging directory. Only the files of
        the skill reach the extracted copy, and the staging directory is always
        removed.
        """
        url = validate_repo_url(repo_url)
        pin = validate_commit_pin(commit or "")
        root = Path(self.settings.SKILLS_ROOT).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(root)))
        try:
            await asyncio.to_thread(
                _run_git, ["clone", "--quiet", "--no-checkout", url, str(staging)]
            )
            await asyncio.to_thread(
                _run_git,
                ["-C", str(staging), "fetch", "--quiet", "--depth", "1", "origin", pin],
            )
            await asyncio.to_thread(
                _run_git,
                ["-C", str(staging), "checkout", "--quiet", "--detach", pin],
            )
            resolved = await asyncio.to_thread(
                _run_git, ["-C", str(staging), "rev-parse", "HEAD"]
            )
            manifest = await asyncio.to_thread(read_manifest, staging)
            target = resolve_skill_dir(root, manifest.name)
            files = await asyncio.to_thread(collect_skill_files, staging)
            await asyncio.to_thread(self._extract, files, target)
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)

        return await self.save_skill(
            SkillCreateDTO(
                name=manifest.name,
                description=manifest.description,
                version=manifest.version,
                source="git",
                commit=resolved or pin,
                storage_path=str(target),
            )
        )

    @staticmethod
    def _extract(files: dict[str, bytes], target: Path) -> None:
        """Write the files to ``target`` and replace an earlier copy.

        The copy is built next to the target, then renamed. A failure leaves the
        earlier copy in place.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.parent / f".{target.name}.{uuid4().hex}.new"
        try:
            for relative, content in files.items():
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            if target.exists():
                shutil.rmtree(target)
            staged.replace(target)
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    async def load_into_sandbox(
        self,
        name: str,
        sandbox_runner: SandboxRunnerProtocol,
        container_id: str,
    ) -> list[str]:
        """Copy one installed skill to ``/workspace/skills/<name>``.

        Returns:
            The relative names of the files that the copy wrote, in order.
        """
        skill_dir = resolve_skill_dir(self.settings.SKILLS_ROOT, name)
        files = await asyncio.to_thread(collect_skill_files, skill_dir)
        return await write_skill_files(files, name, sandbox_runner, container_id)

    async def inject_skills_into_sandbox(
        self,
        skill_names: list[str],
        container_id: str,
        sandbox_runner: SandboxRunnerProtocol,
    ) -> None:
        """Copy each named skill into the sandbox workspace."""
        for name in skill_names:
            await self.load_into_sandbox(name, sandbox_runner, container_id)

    @staticmethod
    def _to_dto(row: SkillModel) -> SkillDTO:
        """Convert one registry row to a data transfer object."""
        return SkillDTO(
            id=row.id,
            name=row.name,
            description=row.description,
            version=row.version,
            source=row.source,
            commit=row.commit,
            enabled=row.enabled,
            storage_path=row.storage_path,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


async def write_skill_files(
    files: dict[str, bytes],
    name: str,
    sandbox_runner: SandboxRunnerProtocol,
    container_id: str,
) -> list[str]:
    """Write a skill file map below ``/workspace/skills/<name>``.

    The name is validated before it becomes a container path. The runner
    resolves the path below ``/workspace`` and rejects an escape.

    Returns:
        The relative file names that the copy wrote, in sorted order.
    """
    safe_name = validate_skill_name(name)
    prefix = f"/workspace/skills/{safe_name}"
    written: list[str] = []
    for relative in sorted(files):
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValidationError(f"invalid skill file path: {relative!r}")
        await sandbox_runner.write_file(
            container_id, f"{prefix}/{relative}", files[relative]
        )
        written.append(relative)
    return written


__all__ = [
    "COMMIT_SHA_PATTERN",
    "MANIFEST_FILENAME",
    "SKILL_NAME_PATTERN",
    "SkillManifest",
    "SkillsService",
    "collect_skill_files",
    "parse_frontmatter",
    "parse_manifest",
    "read_manifest",
    "resolve_skill_dir",
    "validate_commit_pin",
    "validate_repo_url",
    "validate_skill_name",
    "write_skill_files",
]
