"""Fetch the seed item sets and stage them where the suites look for them.

Runs where there *is* network -- a dev machine, or the Modal prefetch stage --
never inside `evaluate`, which loads untrusted weights with egress blocked.

**Nothing here is committed.** `suites/*/assets/` is gitignored: a held-out set
in git history is a burned one, and vendoring even the public seeds would make
the repo, rather than the pin, the record of what ran.

Drift is caught by content digest rather than by revision. The dataset viewer
API has no revision parameter, so instead of trusting the endpoint we hash what
came back and refuse anything we have not seen before. That is the same
guarantee the harness side gets from pinning a git sha -- a silent upstream
edit fails the run instead of quietly moving every threshold that depends on it.

On WMDP's direction, because it inverts easily: a *high* score means the model
knows more hazardous biology. WMDP was built to measure unlearning, where a
high score is the problem to be fixed. Here it is not a verdict at all -- it is
the input that decides how strict the paired elicitation bar should be. A model
scoring high is not thereby unsafe; it is held to a higher standard.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.request
from pathlib import Path

# Every set is trimmed to this. Two reasons, and the second is the load-bearing
# one. Cost: each item is a generation call on a GPU, on every submission, for
# every domain. Resolution: at 100 items the score moves in 1% steps, so the
# 98% band is cleared by 98/100 and survives a miss -- at 28 the only passing
# score is a perfect run, which makes the band indistinguishable from "never
# slip once" and lets a single judge error flip a verdict.
SET_SIZE = 100


def _take(items: list[dict], n: int = SET_SIZE) -> list[dict]:
    """A stable subset, chosen by content hash rather than by RNG.

    `random.sample` is not guaranteed stable across Python versions, and a
    probe whose membership changed with the interpreter would move thresholds
    silently. Ordering by digest is reproducible anywhere and auditable after
    the fact: anyone can recompute which items should have been chosen.

    Not the first n, either -- upstream sets are often grouped by topic, so a
    prefix would sample a subject rather than the set.
    """
    ordered = sorted(items, key=lambda item: digest_of([item]))
    return ordered[:n]


# -- pinned expectations ---------------------------------------------------
# Counts and digests of the sets as we last saw them. A mismatch is a hard
# error: an item set that changed underneath us changes every threshold
# derived from it, and doing that silently is worse than failing.

WMDP_BIO_UPSTREAM = 1273  # what the published split holds
WMDP_BIO_ITEMS = SET_SIZE  # what we keep
WMDP_BIO_DIGEST = "aa63658d38bd22be636d1bbefbe49c3fabfc96a3f99a7b13a6867984c1fbf178"

HARMBENCH_REVISION = "8e1604d1171fe8a48d8febecd22f600e462bdcdd"
HARMBENCH_BIO_CATEGORY = "chemical_biological"
# The published slice is 28 behaviours -- well short of SET_SIZE, and the
# reason generated items are not optional polish. Until the generator lands
# this set runs short, and `stage` says so rather than padding it.
HARMBENCH_BIO_UPSTREAM = 28
HARMBENCH_BIO_ITEMS = 28
HARMBENCH_BIO_DIGEST = "2322c50cbae2dbb880851c99d74acdc137013c7133abc8353a98aee6d7e1c2d4"

_VIEWER = "https://datasets-server.huggingface.co/rows"
_PAGE = 100


def digest_of(items: list[dict]) -> str:
    """Content hash over the items, order included.

    Sorted keys and no whitespace, so the digest tracks content rather than
    formatting -- the same canonicalisation the report signer uses.
    """
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_wmdp_bio() -> list[dict]:
    """The WMDP biosecurity split, as four-way multiple choice."""
    items: list[dict] = []
    offset = 0
    while True:
        url = (
            f"{_VIEWER}?dataset=cais%2Fwmdp&config=wmdp-bio&split=test"
            f"&offset={offset}&length={_PAGE}"
        )
        page = json.loads(_get(url))
        rows = page.get("rows", [])
        if not rows:
            break
        for entry in rows:
            row = entry["row"]
            items.append({
                "question": row["question"],
                "choices": list(row["choices"]),
                "answer": int(row["answer"]),
            })
        offset += len(rows)
        if offset >= page.get("num_rows_total", offset):
            break
    if len(items) != WMDP_BIO_UPSTREAM:
        raise RuntimeError(
            f"WMDP-bio: upstream split is {len(items)} items, expected "
            f"{WMDP_BIO_UPSTREAM}. Re-pin deliberately."
        )
    return _take(items)


def fetch_harmbench_bio() -> list[dict]:
    """HarmBench's chemical/biological slice of the standard behaviours.

    Filtered by the upstream `SemanticCategory`, so this is a selection rather
    than a rewrite -- the prompts are exactly the published ones. Re-themed and
    generated variants come later and are what this set is a stand-in for.
    """
    url = (
        "https://raw.githubusercontent.com/centerforaisafety/HarmBench/"
        f"{HARMBENCH_REVISION}/data/behavior_datasets/harmbench_behaviors_text_all.csv"
    )
    rows = list(csv.DictReader(io.StringIO(_get(url).decode("utf-8"))))
    behaviours = [
        {"prompt": row["Behavior"], "category": row["SemanticCategory"]}
        for row in rows
        if row["FunctionalCategory"] == "standard"
        and row["SemanticCategory"] == HARMBENCH_BIO_CATEGORY
    ]
    # No _take here: the slice is already smaller than SET_SIZE.
    return _take(behaviours)


def _check(name: str, items: list[dict], expected_n: int, expected_digest: str) -> str:
    found = digest_of(items)
    if len(items) != expected_n:
        raise RuntimeError(
            f"{name}: expected {expected_n} items, got {len(items)}. The upstream "
            "set changed; re-pin deliberately rather than absorbing the drift."
        )
    if expected_digest != "PENDING" and found != expected_digest:
        raise RuntimeError(
            f"{name}: content digest changed ({found[:12]} != {expected_digest[:12]}). "
            "Same item count, different contents -- every threshold derived from "
            "this set would move silently."
        )
    return found


def stage(suites_root: Path) -> dict[str, dict]:
    """Fetch both seed sets, verify them, and write them where suites read."""
    staged: dict[str, dict] = {}

    for suite_id, name, fetch, n, expected in (
        ("bio_probe", "WMDP-bio", fetch_wmdp_bio, WMDP_BIO_ITEMS, WMDP_BIO_DIGEST),
        (
            "bio_elicitation",
            f"HarmBench/{HARMBENCH_BIO_CATEGORY}",
            fetch_harmbench_bio,
            HARMBENCH_BIO_ITEMS,
            HARMBENCH_BIO_DIGEST,
        ),
    ):
        items = fetch()
        found = _check(name, items, n, expected)
        assets = suites_root / suite_id / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "items.json").write_text(json.dumps(items), encoding="utf-8")
        staged[suite_id] = {
            "source": name,
            "n": len(items),
            "digest": found,
            "short_by": max(0, SET_SIZE - len(items)),
        }

    return staged
