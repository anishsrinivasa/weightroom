"""Bounded-memory ZIP64 streaming for entitled artifact downloads."""

from __future__ import annotations

import io
import queue
import threading
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

_CHUNK = 8 * 1024 * 1024
_DONE = object()


class ChunkSource(Protocol):
    def iter_bytes(self, key: str, chunk_size: int = _CHUNK) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class ZipEntry:
    key: str
    archive_name: str


def safe_archive_name(value: str) -> str:
    """Normalize an artifact path while preventing ZIP path traversal."""
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ".." in path.parts:
        raise ValueError(f"unsafe archive path: {value!r}")
    return str(path)


class _QueueSink(io.RawIOBase):
    def __init__(self, chunks: queue.Queue[object], stopped: threading.Event) -> None:
        super().__init__()
        self._chunks = chunks
        self._stopped = stopped
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def write(self, data: bytes | bytearray | memoryview) -> int:
        payload = bytes(data)
        if not payload:
            return 0
        while not self._stopped.is_set():
            try:
                self._chunks.put(payload, timeout=0.1)
                self._position += len(payload)
                return len(payload)
            except queue.Full:
                continue
        raise BrokenPipeError("ZIP download was cancelled")


def stream_zip(
    source: ChunkSource,
    entries: Iterable[ZipEntry],
    *,
    chunk_size: int = _CHUNK,
) -> Iterator[bytes]:
    """Yield one ZIP archive while keeping only a few source chunks in memory."""
    chunks: queue.Queue[object] = queue.Queue(maxsize=4)
    stopped = threading.Event()

    def offer(item: object) -> bool:
        while not stopped.is_set():
            try:
                chunks.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        failure: BaseException | None = None
        try:
            sink = _QueueSink(chunks, stopped)
            with zipfile.ZipFile(
                sink,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for entry in entries:
                    with archive.open(entry.archive_name, mode="w", force_zip64=True) as target:
                        for chunk in source.iter_bytes(entry.key, chunk_size):
                            target.write(chunk)
        except BrokenPipeError:
            return
        except BaseException as exc:  # delivered to the response iterator
            failure = exc
        finally:
            if failure is not None and not offer(failure):
                return
            offer(_DONE)

    producer = threading.Thread(target=produce, name="artifact-zip-stream", daemon=True)
    producer.start()
    try:
        while True:
            item = chunks.get()
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            yield item  # type: ignore[misc]
    finally:
        stopped.set()
