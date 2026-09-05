"""Ingest plane: resolve a model reference, materialise it, hash it.

Runs with network ON. No untrusted code executes here -- this step only moves
bytes. Anything that *loads* a weight file belongs in the sandboxed eval plane.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from keystone.schema import FileEntry, Source, SourceKind, Subject

_CHUNK = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def manifest_digest(files: list[FileEntry]) -> str:
    """Content address for the whole artifact. Order-independent."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.path):
        h.update(f"{f.path}:{f.sha256}\n".encode())
    return h.hexdigest()


def hash_tree(root: Path) -> list[FileEntry]:
    entries: list[FileEntry] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".cache" in p.parts or ".git" in p.parts:
            continue
        entries.append(
            FileEntry(
                path=p.relative_to(root).as_posix(),
                size_bytes=p.stat().st_size,
                sha256=sha256_file(p),
            )
        )
    return entries


def resolve_and_download(ref: str, dest: Path, revision: str | None = None) -> tuple[Path, str]:
    """Materialise a HF repo. Returns (local_path, resolved_revision).

    Everything is downloaded, including pickle-format weights -- scanning those
    is a core deliverable, so excluding them to save bandwidth would defeat the
    purpose.
    """
    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(ref, revision=revision)
    resolved = info.sha or revision or "main"

    local = snapshot_download(
        repo_id=ref,
        revision=resolved,
        local_dir=str(dest),
        token=os.environ.get("HF_TOKEN"),
    )
    return Path(local), resolved


def build_subject(ref: str, local: Path, revision: str) -> Subject:
    files = hash_tree(local)
    return Subject(
        source=Source(kind=SourceKind.HF, ref=ref, revision=revision),
        artifact_digest=manifest_digest(files),
        files=files,
        total_bytes=sum(f.size_bytes for f in files),
    )
