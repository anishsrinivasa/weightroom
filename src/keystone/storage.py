"""Durable artifact store.

Once creators upload models to us, we are the origin -- there is no upstream to
re-fetch from. The Modal Volume goes back to being what it is (a cache for
certification jobs) and this becomes the system of record.

Content-addressed by `artifact_digest`, so the store is immutable and two
uploads of identical weights cost one copy. Integrity is verified on read: a
file whose sha256 does not match its manifest entry is a hard error, not a
warning. That check is what lets a buyer trust a download without trusting us.
"""

from __future__ import annotations

import abc
import hashlib
import os
import shutil
from pathlib import Path

from keystone.schema import FileEntry

_CHUNK = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def artifact_key(digest: str, relpath: str) -> str:
    return f"artifacts/{digest}/{relpath}"


class ArtifactStore(abc.ABC):
    """Blobs only. Metadata lives in Postgres, never here."""

    @abc.abstractmethod
    def put(self, key: str, source: Path) -> None: ...

    @abc.abstractmethod
    def get(self, key: str, dest: Path) -> None: ...

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def presign_get(self, key: str, ttl_s: int = 3600) -> str:
        """Time-limited download URL. Entitlement is checked before minting one."""

    # -- higher level ------------------------------------------------------

    def upload_tree(self, root: Path, files: list[FileEntry], digest: str) -> int:
        """Persist a verified tree. Returns bytes actually written (0 on full dedup)."""
        written = 0
        for entry in files:
            key = artifact_key(digest, entry.path)
            if self.exists(key):
                continue
            local = root / entry.path
            actual = _sha256(local)
            if actual != entry.sha256:
                raise ValueError(
                    f"manifest mismatch for {entry.path}: "
                    f"expected {entry.sha256[:12]}, got {actual[:12]}"
                )
            self.put(key, local)
            written += entry.size_bytes
        return written

    def materialize(self, digest: str, files: list[FileEntry], dest: Path) -> None:
        """Reconstruct a tree locally, verifying every file against the manifest."""
        dest.mkdir(parents=True, exist_ok=True)
        for entry in files:
            target = dest / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and _sha256(target) == entry.sha256:
                continue  # cache hit
            self.get(artifact_key(digest, entry.path), target)
            actual = _sha256(target)
            if actual != entry.sha256:
                target.unlink(missing_ok=True)
                raise ValueError(
                    f"integrity failure on {entry.path}: "
                    f"expected {entry.sha256[:12]}, got {actual[:12]}"
                )


class LocalStore(ArtifactStore):
    """Filesystem-backed. For tests and local development only."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, source: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def get(self, key: str, dest: Path) -> None:
        source = self._path(key)
        if not source.is_file():
            raise FileNotFoundError(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presign_get(self, key: str, ttl_s: int = 3600) -> str:
        return self._path(key).as_uri()


class S3Store(ArtifactStore):
    """S3-compatible. Cloudflare R2 in production (set `endpoint_url`)."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str = "auto",
    ) -> None:
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url or os.environ.get("R2_ENDPOINT_URL"),
            region_name=region,
            aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        )

    def put(self, key: str, source: Path) -> None:
        self._s3.upload_file(str(source), self.bucket, key)

    def get(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, key, str(dest))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)

    def presign_get(self, key: str, ttl_s: int = 3600) -> str:
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_s
        )


def from_env() -> ArtifactStore:
    """R2 when configured, local filesystem otherwise."""
    bucket = os.environ.get("KEYSTONE_BUCKET")
    if bucket:
        return S3Store(bucket)
    return LocalStore(Path(os.environ.get("KEYSTONE_LOCAL_STORE", ".keystone-store")))
