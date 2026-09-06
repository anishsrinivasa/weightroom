"""Which directory a suite reads its items from.

The sandbox fetches some corpora at runtime, while it still has network, and
drops them under a staging root. Other suites carry their items in the image.
Both kinds run in the same pass, so the staging root cannot be applied blindly
-- doing so pointed every conditioning pair at a directory that only ever holds
the public benchmarks, and they loaded zero items on every run.
"""

from __future__ import annotations

from pathlib import Path

from keystone.registry import SUITES_ROOT as SUITES
from keystone.run import collect_suites
from keystone.schema import Capabilities, Modality


class FakeClient:
    async def chat(self, messages, **kwargs) -> str:
        return "A"


def _seen_assets_dirs(tmp_path: Path, assets_root: Path | None) -> dict[str, Path]:
    """Run the selector and record the assets dir handed to each suite."""
    from keystone.suites import SuiteContext

    seen: dict[str, Path] = {}
    real_init = SuiteContext.__init__

    def spy(self, **kwargs):
        real_init(self, **kwargs)
        # The context does not carry the suite id. Both layouts end in it:
        # `<suites_root>/<id>/assets` and `<assets_root>/<id>`.
        path = Path(kwargs["assets_dir"])
        seen[path.parent.name if path.name == "assets" else path.name] = path

    SuiteContext.__init__ = spy  # type: ignore[method-assign]
    try:
        collect_suites(
            FakeClient(),
            model_name="fake",
            capabilities=Capabilities(chat=True),
            modality=[Modality.TEXT],
            suites_root=SUITES,
            scratch_dir=tmp_path / "scratch",
            assets_root=assets_root,
            only=[],
        )
    finally:
        SuiteContext.__init__ = real_init  # type: ignore[method-assign]
    return seen


def test_a_suite_without_staged_assets_keeps_its_own(tmp_path: Path) -> None:
    """The conditioning pairs ship their items in the image. A staging root
    that holds only public benchmarks must not redirect them."""
    staging = tmp_path / "staged"
    staging.mkdir()
    # Only one suite has fetched assets, as in a real sandbox run.
    (staging / "mmlu_pro").mkdir()
    (staging / "mmlu_pro" / "tasks.json").write_text("[]", encoding="utf-8")

    seen = _seen_assets_dirs(tmp_path, staging)

    if "mmlu_pro" in seen:
        assert seen["mmlu_pro"] == staging / "mmlu_pro"
    for suite_id, path in seen.items():
        if suite_id == "mmlu_pro":
            continue
        assert path == SUITES / suite_id / "assets", (
            f"{suite_id} was redirected to a staging root that has no items for it"
        )


def test_no_staging_root_means_every_suite_uses_its_own(tmp_path: Path) -> None:
    for suite_id, path in _seen_assets_dirs(tmp_path, None).items():
        assert path == SUITES / suite_id / "assets"


def test_the_two_suite_families_use_different_item_filenames() -> None:
    """Why crossing their directories fails silently rather than loudly: a
    conditioning suite reads items.json, a public benchmark reads tasks.json,
    so a misrouted suite finds an existing directory with nothing it wants."""
    conditioning = (SUITES / "bio_probe" / "suite.py").read_text(encoding="utf-8")
    public = (SUITES / "mmlu_pro" / "suite.py").read_text(encoding="utf-8")
    assert "items.json" in conditioning and "tasks.json" not in conditioning
    assert "tasks.json" in public and "items.json" not in public
