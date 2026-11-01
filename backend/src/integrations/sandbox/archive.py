"""Workspace archive and restore for the sandbox.

An archive is a tar file plus a SHA-256 checksum. :func:`restore_archive` verifies the
checksum before it reads or extracts a single member, so a corrupted or swapped tarball never
reaches a container.

Archives written by :func:`create_archive` are deterministic: names are sorted, metadata is
normalized, and the gzip header carries no timestamp. Two archives of the same tree therefore
have the same checksum, which a round trip test can assert.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

CHUNK_BYTES = 1024 * 1024
SIDECAR_SUFFIX = ".sha256"


class ArchiveError(RuntimeError):
    """Base error for archive and restore failures."""


class ChecksumMismatch(ArchiveError):
    """Raised when an archive checksum does not match the expected value."""


@dataclass(frozen=True)
class ArchiveRecord:
    """A tar archive and its checksum."""

    path: str
    sha256: str
    size_bytes: int

    @property
    def checksum(self) -> str:
        """Alias for the archive SHA-256."""
        return self.sha256


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    """Return the SHA-256 hex digest of a byte string."""
    return hashlib.sha256(content).hexdigest()


def record_archive(tar_path: str | Path) -> ArchiveRecord:
    """Hash an archive that already exists and return its record."""
    path = Path(tar_path)
    if not path.is_file():
        raise ArchiveError(f"archive does not exist: {path}")
    return ArchiveRecord(path=str(path), sha256=sha256_file(path), size_bytes=path.stat().st_size)


def checksum_sidecar_path(tar_path: str | Path) -> Path:
    """Return the sidecar checksum path for an archive."""
    path = Path(tar_path)
    return path.with_name(path.name + SIDECAR_SUFFIX)


def write_checksum_sidecar(record: ArchiveRecord) -> Path:
    """Write the checksum sidecar next to the archive."""
    sidecar = checksum_sidecar_path(record.path)
    sidecar.write_text(f"{record.sha256}  {Path(record.path).name}\n", encoding="utf-8")
    return sidecar


def read_checksum_sidecar(tar_path: str | Path) -> str | None:
    """Read the checksum sidecar for an archive, or return None when absent."""
    sidecar = checksum_sidecar_path(tar_path)
    if not sidecar.is_file():
        return None
    fields = sidecar.read_text(encoding="utf-8").split()
    if not fields:
        raise ArchiveError(f"checksum sidecar is empty: {sidecar}")
    return fields[0]


def verify_archive(tar_path: str | Path, expected_sha256: str) -> ArchiveRecord:
    """Verify an archive against a checksum before any member is read."""
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ArchiveError("a full SHA-256 checksum is required to verify an archive")
    record = record_archive(tar_path)
    if record.sha256 != expected_sha256.lower():
        raise ChecksumMismatch(
            f"archive checksum mismatch for {record.path}: "
            f"expected {expected_sha256.lower()}, got {record.sha256}"
        )
    return record


def _normalized_mode(path: Path) -> int:
    """Return a stable mode: 0o755 for directories, 0o644 for files."""
    return 0o755 if path.is_dir() else 0o644


def _sorted_entries(root: Path) -> list[Path]:
    """Return every entry below root in a stable, depth first order."""
    entries: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        current_path = Path(current)
        for name in dirnames:
            entries.append(current_path / name)
        for name in filenames:
            entries.append(current_path / name)
    return entries


def create_archive(
    workspace_dir: str | Path,
    tar_path: str | Path,
    *,
    write_checksum: bool = True,
) -> ArchiveRecord:
    """Archive a directory to a deterministic gzip tarball and record its checksum."""
    source = Path(workspace_dir)
    if not source.is_dir():
        raise ArchiveError(f"workspace directory does not exist: {source}")
    target = Path(tar_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for entry in _sorted_entries(source):
                    relative = entry.relative_to(source).as_posix()
                    info = tar.gettarinfo(str(entry), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = _normalized_mode(entry)
                    if entry.is_symlink():
                        # Symlinks are archived as links, never dereferenced.
                        info.linkname = os.readlink(entry)
                        tar.addfile(info)
                    elif entry.is_file():
                        with open(entry, "rb") as handle:
                            tar.addfile(info, handle)
                    elif entry.is_dir():
                        tar.addfile(info)

    record = record_archive(target)
    if write_checksum:
        write_checksum_sidecar(record)
    return record


def _iter_members(tar: tarfile.TarFile) -> Iterator[tarfile.TarInfo]:
    """Yield archive members after rejecting unsafe paths and entry types."""
    for member in tar.getmembers():
        name = member.name
        if not name or name.startswith("/") or name.startswith("../"):
            raise ArchiveError(f"archive member has an unsafe path: {name!r}")
        if ".." in Path(name).parts:
            raise ArchiveError(f"archive member has an unsafe path: {name!r}")
        if member.isdev():
            raise ArchiveError(f"archive member is a device: {name!r}")
        if member.issym() or member.islnk():
            link = member.linkname
            if not link or link.startswith("/") or ".." in Path(link).parts:
                raise ArchiveError(f"archive member has an unsafe link: {name!r}")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ArchiveError(f"archive member has an unsupported type: {name!r}")
        yield member


def restore_archive(
    tar_path: str | Path,
    target_dir: str | Path,
    *,
    expected_sha256: str,
) -> ArchiveRecord:
    """Verify an archive checksum, then extract it into target_dir.

    The checksum check runs before the first member is read. A mismatch leaves target_dir
    untouched.
    """
    record = verify_archive(tar_path, expected_sha256)
    destination = Path(target_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(record.path, mode="r:*") as tar:
        members = list(_iter_members(tar))
        for member in members:
            resolved = (destination / member.name).resolve()
            if destination.resolve() not in resolved.parents and resolved != destination.resolve():
                raise ArchiveError(f"archive member escapes the target: {member.name!r}")
        tar.extractall(path=destination, members=members, filter="data")
    return record
