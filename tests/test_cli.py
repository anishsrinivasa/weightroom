from contextlib import contextmanager
import json
from pathlib import Path
import re
from types import SimpleNamespace

from typer.testing import CliRunner

from keystone.cli import _has_pending_certification, app
from keystone.storage import artifact_key


def test_dev_command_is_available_for_the_managed_local_stack() -> None:
    result = CliRunner().invoke(app, ["dev", "--help"])
    plain_output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)

    assert result.exit_code == 0
    assert "local API and certification worker" in plain_output
    assert "--worker-interval" in plain_output


def test_idle_worker_checks_local_queue_before_opening_modal() -> None:
    class Store:
        @contextmanager
        def session(self):
            yield object()

        def listings_in_state(self, session, state):
            return [SimpleNamespace(id="listing-1")]

    store = Store()
    assert _has_pending_certification(store)
    assert _has_pending_certification(store, listing_id="listing-1")
    assert not _has_pending_certification(store, listing_id="listing-2")


def test_sample_model_defaults_to_grayswan_and_stages_the_artifact(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "sample-model"
    artifact_store = tmp_path / "store"
    destination.mkdir()
    (destination / "stale.txt").write_text("old sample", encoding="utf-8")
    downloaded: dict[str, str] = {}

    def fake_download(*, repo_id, local_dir, allow_patterns):
        downloaded["repo_id"] = repo_id
        root = Path(local_dir)
        (root / "config.json").write_text("{}", encoding="utf-8")
        header = json.dumps(
            {"weight": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}}
        ).encode()
        (root / "model.safetensors").write_bytes(
            len(header).to_bytes(8, "little") + header + bytes(24)
        )
        return local_dir

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)

    result = CliRunner().invoke(
        app,
        ["sample-model", "--dest", str(destination), "--store", str(artifact_store)],
    )

    assert result.exit_code == 0, result.output
    assert downloaded["repo_id"] == "GraySwanAI/Llama-3-8B-Instruct-RR"
    assert not (destination / "stale.txt").exists()
    manifest = json.loads((destination / "files.json").read_text(encoding="utf-8"))
    assert manifest["source"] == downloaded["repo_id"]
    assert manifest["parameter_count"] == 6
    assert [entry["path"] for entry in manifest["files"]] == [
        "config.json",
        "model.safetensors",
    ]
    assert (
        artifact_store / artifact_key(manifest["digest"], "model.safetensors")
    ).is_file()
