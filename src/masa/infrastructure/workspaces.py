"""Snapshot trusted local sources; refuse links and retain content identity."""

import hashlib
import os
from pathlib import Path
import shutil

from masa.domain.models import MasaError, digest


EXCLUDED = {".git", ".masa", ".cache", ".tools", ".venv", "__pycache__"}
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 4000


def manifest(root: Path) -> dict[str, str]:
    """生成有界文件哈希清单并拒绝链接。 Build a bounded hash manifest and reject links."""
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise MasaError("workspace must be a real directory")
    entries: dict[str, str] = {}
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(base) / name
            if p.is_symlink() or p.is_junction():
                raise MasaError(f"workspace contains a link/reparse point: {p}")
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(files):
            if name in EXCLUDED or name == ".env" or name.startswith(".env."):
                continue
            path = Path(base) / name
            if not path.is_file():
                raise MasaError(f"not a regular file: {path}")
            total += path.stat().st_size
            if total > MAX_BYTES or len(entries) >= MAX_FILES:
                raise MasaError("P0 workspace exceeds 32 MiB / 4000 files")
            entries[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entries


def copy_snapshot(source: Path, destination: Path) -> tuple[str, dict[str, str]]:
    """复制并核对源及副本一致性。 Copy sources and verify both source and copy identities."""
    source = source.absolute()
    if source.is_symlink() or source.is_junction():
        raise MasaError("source may not be a link")
    source = source.resolve()
    if destination.resolve().is_relative_to(source):
        raise MasaError("state directory must be outside the source repository")
    before = manifest(source)
    if "go.mod" not in before:
        raise MasaError("P0 requires a Go module root (go.mod)")
    destination.mkdir(parents=True, exist_ok=False)
    for relative in before:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    copied = manifest(destination)
    if before != copied or before != manifest(source):
        raise MasaError("source changed while creating snapshot")
    return digest(copied), copied


def verify_snapshot(root: Path, expected: str) -> None:
    """拒绝与证据版本不符的工作区。 Reject a workspace that differs from the evidence version."""
    if digest(manifest(root)) != expected:
        raise MasaError("snapshot_mismatch: workspace changed; refusing to reuse evidence")
