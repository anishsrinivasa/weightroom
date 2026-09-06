"""Upload-to-Modal boundary tests. No network or Modal account required."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from keystone.ingest import hash_tree, manifest_digest
from keystone.runner import modal_app
from keystone.runner.modal_app import (
    _safety_cache_env,
    _download_verified,
    _inspect_upload,
    _safe_upload_path,
)


def test_safety_cache_disables_xet_for_modal_volume_commits():
    assert _safety_cache_env()["HF_HUB_DISABLE_XET"] == "1"


def test_modal_upload_inspection_preserves_uploaded_identity(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"architectures":["LlamaForCausalLM"],"model_type":"llama"}'
    )
    (tmp_path / "model.safetensors").write_bytes(b"safe-placeholder")
    files = hash_tree(tmp_path)
    digest = manifest_digest(files)

    result = _inspect_upload(
        digest,
        [entry.model_dump(mode="json") for entry in files],
        tmp_path,
        cached=False,
    )

    assert result["cache_key"] == f"upload@{digest}"
    assert result["subject"]["source"] == {
        "kind": "upload",
        "ref": f"artifact:{digest}",
        "revision": digest,
    }
    assert result["subject"]["artifact_digest"] == digest
    assert result["bytes_transferred"] == sum(entry.size_bytes for entry in files)


def test_modal_upload_inspection_rejects_tampered_bytes(tmp_path: Path) -> None:
    content = b"original"
    target = tmp_path / "config.json"
    target.write_bytes(content)
    manifest = [{
        "path": "config.json",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }]
    digest = manifest_digest(hash_tree(tmp_path))
    target.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="integrity check failed"):
        _inspect_upload(digest, manifest, tmp_path, cached=False)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "weights/../../secret"])
def test_modal_upload_paths_cannot_escape_cache(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="unsafe|escapes"):
        _safe_upload_path(tmp_path, path)


def test_modal_download_is_hash_checked_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"verified bytes"
    monkeypatch.setattr(modal_app, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    target = tmp_path / "weights.safetensors"

    _download_verified(
        "https://objects.example/weights",
        target,
        expected_size=len(body),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )

    assert target.read_bytes() == body


def test_modal_download_rejects_oversized_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modal_app, "urlopen", lambda *args, **kwargs: io.BytesIO(b"too big"))
    target = tmp_path / "weights.safetensors"

    with pytest.raises(ValueError, match="exceeds declared size"):
        _download_verified(
            "https://objects.example/weights",
            target,
            expected_size=3,
            expected_sha256="0" * 64,
        )

    assert not target.exists()
    assert not (tmp_path / ".weights.safetensors.partial").exists()
