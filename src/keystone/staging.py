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
import time
import urllib.error
import urllib.request
from pathlib import Path

from keystone.generation import (
    ELICITATION_TRANSFORMS,
    FRAMING_COUNT,
    PROBE_TRANSFORMS,
    expand,
    rotate,
)

# Every set is trimmed to this. Two reasons, and the second is the load-bearing
# one. Cost: each item is a generation call on a GPU, on every submission, for
# every domain. Resolution: at 100 items the score moves in 1% steps, so the
# 98% band is cleared by 98/100 and survives a miss -- at 28 the only passing
# score is a perfect run, which makes the band indistinguishable from "never
# slip once" and lets a single judge error flip a verdict.
SET_SIZE = 100


# -- pinned expectations ---------------------------------------------------
# Counts and digests of the sets as we last saw them. A mismatch is a hard
# error: an item set that changed underneath us changes every threshold
# derived from it, and doing that silently is worse than failing.

WMDP_BIO_UPSTREAM = 1273  # what the published split holds
WMDP_BIO_ITEMS = SET_SIZE  # what we keep
WMDP_BIO_DIGEST = "e5d5ebc327252949ade6592da696ba0f7e87d6fa972dd1a6a23f31a02ac74ae1"

HARMBENCH_REVISION = "8e1604d1171fe8a48d8febecd22f600e462bdcdd"
HARMBENCH_BIO_CATEGORY = "chemical_biological"
# The published slice is 28 behaviours -- well short of SET_SIZE, and the
# reason generated items are not optional polish. Until the generator lands
# this set runs short, and `stage` says so rather than padding it.
HARMBENCH_BIO_UPSTREAM = 28
HARMBENCH_BIO_ITEMS = 28
HARMBENCH_BIO_DIGEST = "d5ae7bec95c29bfa326da0d6e24ee703b78c23d19f6694599757a341511f99f1"

_VIEWER = "https://datasets-server.huggingface.co/rows"
_PAGE = 100

# Raw upstream pools, cached so re-staging a rotation costs nothing. The viewer
# API rate-limits, and re-downloading 1273 rows to reshuffle 100 of them is
# waste anyway. Gitignored: the cache is a convenience, the pin is the record.
CACHE_DIR = Path(".keystone-cache")


def _cached(name: str, fetch):
    """Fetch once, reuse thereafter. Delete the file to force a refresh."""
    path = CACHE_DIR / f"{name}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    items = fetch()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items), encoding="utf-8")
    return items


def digest_of(items: list[dict]) -> str:
    """Content hash over the items, order included.

    Sorted keys and no whitespace, so the digest tracks content rather than
    formatting -- the same canonicalisation the report signer uses.
    """
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get(url: str, attempts: int = 6) -> bytes:
    """Fetch with backoff. The viewer API rate-limits a paginated pull.

    Thirteen pages in quick succession is enough to draw a 429, and failing the
    whole stage on one throttled page would be a self-inflicted outage.
    """
    delay = 2.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            retriable = exc.code == 429 or 500 <= exc.code < 600
            if not retriable or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def fetch_wmdp_bio() -> list[dict]:
    """The WMDP biosecurity split, as four-way multiple choice."""
    return _cached("wmdp-bio", _download_wmdp_bio)


def _download_wmdp_bio() -> list[dict]:
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
    return items


def fetch_harmbench_bio() -> list[dict]:
    """HarmBench's chemical/biological slice of the standard behaviours.

    Filtered by the upstream `SemanticCategory`, so this is a selection rather
    than a rewrite -- the prompts are exactly the published ones. Re-themed and
    generated variants come later and are what this set is a stand-in for.
    """
    return _cached(f"harmbench-{HARMBENCH_BIO_CATEGORY}", _download_harmbench_bio)


def _download_harmbench_bio() -> list[dict]:
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
    if len(behaviours) != HARMBENCH_BIO_UPSTREAM:
        raise RuntimeError(
            f"HarmBench/{HARMBENCH_BIO_CATEGORY}: expected "
            f"{HARMBENCH_BIO_UPSTREAM} behaviours, got {len(behaviours)}."
        )
    return behaviours


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


def variant_pool_size(suite_id: str, pool: list[dict]) -> int:
    """How many distinct items this source can ever produce."""
    if suite_id == "bio_elicitation":
        return len(expand(pool, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT))
    return len(pool)


def overlap_floor(pool_size: int, draw: int = SET_SIZE) -> int:
    """Least possible overlap between any two rotations, from arithmetic alone.

    Drawing `draw` items from `pool_size` forces `2*draw - pool_size` to be
    shared no matter how the selection is done. When that floor is high the set
    is not meaningfully fresh, and a rotation counter that implied otherwise
    would be the sort of number this platform exists not to print.
    """
    return max(0, 2 * draw - pool_size)


def build_probe_set(pool: list[dict], rotation: int) -> list[dict]:
    """Rotate, then permute the options.

    The pool is 1273 and the set is 100, so rotation alone gives a fresh probe
    for a long time without generating anything. Permutation is layered on top
    because rotation does not help against an item the model has already seen:
    one that memorised "this question, option C" scores at chance once the
    options move, while one that knows the biology is unaffected.
    """
    drawn = rotate(pool, SET_SIZE, rotation)
    return expand(drawn, PROBE_TRANSFORMS, variants=1)


def build_elicitation_set(pool: list[dict], rotation: int) -> list[dict]:
    """Expand, because the pool is smaller than the target.

    28 behaviours against a target of 100. Without expansion the strictest band
    is cleared only by a perfect run, so the published set is not gateable at
    its own size -- expansion is what makes the bar mean anything.
    """
    expanded = expand(pool, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    return rotate(expanded, SET_SIZE, rotation)


_SETS = (
    ("bio_probe", "WMDP-bio", fetch_wmdp_bio, build_probe_set, WMDP_BIO_DIGEST),
    (
        "bio_elicitation",
        f"HarmBench/{HARMBENCH_BIO_CATEGORY}",
        fetch_harmbench_bio,
        build_elicitation_set,
        HARMBENCH_BIO_DIGEST,
    ),
)


def stage(suites_root: Path, rotation: int = 0) -> dict[str, dict]:
    """Fetch the seed pools, build this rotation's sets, write them out."""
    staged: dict[str, dict] = {}

    for suite_id, name, fetch, build, expected in _SETS:
        pool = fetch()
        items = build(pool, rotation)
        found = digest_of(items)
        # Only rotation 0 is pinned. Later rotations are meant to differ --
        # that is the entire point -- so pinning them would defeat it. What
        # every rotation must hold is the target size, reported below.
        if rotation == 0 and expected != "PENDING" and found != expected:
            raise RuntimeError(
                f"{name}: rotation 0 digest changed ({found[:12]} != "
                f"{expected[:12]}). Same recipe, different output -- every "
                "threshold derived from this set would move silently."
            )
        assets = suites_root / suite_id / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "items.json").write_text(json.dumps(items), encoding="utf-8")
        variants = variant_pool_size(suite_id, pool)
        staged[suite_id] = {
            "source": name,
            "pool": len(pool),
            "variants": variants,
            "n": len(items),
            "digest": found,
            "short_by": max(0, SET_SIZE - len(items)),
            "overlap_floor": overlap_floor(variants, len(items)),
        }

    return staged
