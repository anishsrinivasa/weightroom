from pathlib import Path

from keystone.storage import LocalStore


def test_relative_local_store_mints_absolute_file_urls(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = LocalStore(Path("store"))
    target = store.root / "artifacts" / "digest" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    assert store.presign_get("artifacts/digest/config.json") == target.as_uri()
