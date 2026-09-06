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
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from keystone.conditioning import (
    MAX_ITEMS,
    TYPICAL_BASELINE_N,
    TYPICAL_BASELINE_RATE,
    allowed_deficit,
    items_for_deficit,
)
from keystone.generation import (
    ELICITATION_TRANSFORMS,
    FRAMING_COUNT,
    LEGAL_FRAMING_COUNT,
    LEGAL_TRANSFORMS,
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

# Elicitation sets are staged at the largest budget conditioning can ask for,
# not at SET_SIZE. The suite runs only what the platform requests -- a few
# dozen items for a model that can barely do the domain, over a thousand for
# one that can. Staging the maximum costs disk and no GPU; staging SET_SIZE
# would cap every strict bar at insufficient evidence.
ELICITATION_SET_SIZE = MAX_ITEMS


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
HARMBENCH_BIO_UPSTREAM = 56
HARMBENCH_BIO_ITEMS = 56
HARMBENCH_BIO_DIGEST = "c27cb051cc45a8d9ea49420bbf1afabfb8c14b0978dd6e558bcbba5c7dcd5c63"

# Further seed corpora, fetched from source and pinned the same way. Seeds are
# the binding constraint on precision: an expanded set of N items built from K
# behaviours carries roughly K independent observations, so more framings buy
# freshness and more behaviours buy statistical power.
ADVBENCH_URL = (
    "https://raw.githubusercontent.com/llm-attacks/llm-attacks/"
    "main/data/advbench/harmful_behaviors.csv"
)
STRONGREJECT_URL = (
    "https://raw.githubusercontent.com/alexandrasouly/strongreject/"
    "main/strongreject_dataset/strongreject_dataset.csv"
)

# AdvBench carries no categories, so these select the coding-domain subset.
# Deliberately conservative and deliberately auditable: a behaviour matching
# both patterns is dropped rather than guessed at, because "virus" means two
# different things and an item in the wrong domain measures the wrong thing.
_CODING_PATTERN = (
    r"\b(malware|ransomware|virus that|computer virus|exploit|vulnerabilit|"
    r"hack(ing|er)?|phishing|ddos|keylogger|backdoor|botnet|sql injection|"
    r"spyware|rootkit|worm that)"
)
_BIO_PATTERN = (
    r"\b(bioweapon|biological weapon|pathogen|virus strain|anthrax|smallpox|"
    r"toxin|nerve agent|chemical weapon|poison|ricin|botulinum|bioterror)"
)
_BIO_PATTERN = (
    r"(bioweapon|biological weapon|pathogen|virus strain|anthrax|smallpox|"
    r"toxin|nerve agent|chemical weapon|poison|ricin|botulinum|bioterror)"
)

# StrongREJECT categories that belong to the legal domain. The others --
# violence, hate, sexual content, disinformation -- are fluency harms with no
# capability axis, so they stay on the absolute screen.
_STRONGREJECT_LEGAL = frozenset({"Non-violent crimes", "Illegal goods and services"})

HARMBENCH_CODING_CATEGORY = "cybercrime_intrusion"
HARMBENCH_CODING_UPSTREAM = 183
HARMBENCH_CODING_DIGEST = "1915b2ee53c77d79970ad4fffa8392f871f951bf6bf7eefc1e1ca95c899154e9"

# LegalBench, three tasks that are genuinely binary. Harvey's BigLaw Bench is
# largely proprietary, so it cannot be a probe we run ourselves.
#
# Binary, so guessing pays 50% rather than 25%. That is not a detail: a
# coin-flipper scored against a four-way floor would be credited with a quarter
# of a capability band it does not have, and held to a stricter safety bar for
# knowledge it lacks.
LEGALBENCH_TASKS = (
    ("consumer_contracts_qa", "contract", "question"),
    ("contract_qa", "text", "question"),
    ("citation_prediction_classification", "text", "citation"),
)
LEGALBENCH_UPSTREAM = 584
LEGALBENCH_DIGEST = "1c5c6c6105e96a7626d9620a4e773a3d124586972d1ec907a5d766fdc4eea4c0"

HARMBENCH_LEGAL_CATEGORY = "illegal"
HARMBENCH_LEGAL_UPSTREAM = 174
HARMBENCH_LEGAL_DIGEST = "a500ccc271a355dba05607fae4e175e90b651feaf5fc06da8cc8f92087d173e5"

# The general comparator. Fluency harms -- harassment, misinformation, and the
# rest -- pooled from every source, because a domain gap is only meaningful
# against a baseline built the same way.
#
# The absolute screens cannot serve this purpose. They are plain, unframed
# prompts; the domain sets are framed, contextual and multi-source. Comparing
# them measures how hard the two instruments are rather than how the model
# differs across domains, which is the error the deficit rule was introduced to
# fix and which came back the moment the domain pools were expanded and the
# comparator was not.
GENERAL_CATEGORIES = frozenset(
    {"harassment_bullying", "harmful", "misinformation_disinformation"}
)
GENERAL_UPSTREAM = 416
GENERAL_DIGEST = "218c6c5037b514f5dd8fdf92f3c2a54620d32fbf553568622b802316a22f8eb4"

JBB_URL = (
    "https://datasets-server.huggingface.co/rows?dataset=JailbreakBench%2F"
    "JBB-Behaviors&config=behaviors&split=harmful&offset=0&length=100"
)

SWEBENCH_UPSTREAM = 500
SWEBENCH_DIGEST = "5f9eb3b921f4f68447b6fb47bbddfdeef8cece3042b6a8e43a790e890630b9b6"
# Diffs and issue text are long; four full patches plus a problem statement
# would not fit a sensible prompt. Truncated to the point where the fix is
# still identifiable -- enough to tell the candidates apart, not enough to
# blow the context window.
_PATCH_CHARS = 900
_STATEMENT_CHARS = 1400

_VIEWER = "https://datasets-server.huggingface.co/rows"
_PAGE = 100

# Raw upstream pools, cached so re-staging a rotation costs nothing. The viewer
# API rate-limits, and re-downloading 1273 rows to reshuffle 100 of them is
# waste anyway. Gitignored: the cache is a convenience, the pin is the record.
CACHE_DIR = Path(".keystone-cache")


def _cached(name: str, fetch):
    """Fetch once, reuse thereafter. Delete the file to force a refresh.

    An empty result is never cached. A selector that matches nothing looks
    exactly like a corpus that is empty, and caching that turns a transient
    filter bug into a permanent one -- which is close to what happened when a
    mangled pattern silently matched zero AdvBench rows.
    """
    path = CACHE_DIR / f"{name}.json"
    if path.is_file():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached:
            return cached
        path.unlink(missing_ok=True)
    items = fetch()
    if items:
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


def _harmbench_slice(category: str) -> list[dict]:
    """One semantic category of HarmBench, standard and contextual tracks.

    A selection, not a rewrite: the prompts are exactly the published ones.
    Framing variants are produced later by `generation.expand`.

    The contextual track was previously skipped because it needs the context
    string prepended rather than the behaviour alone. It is worth the handling:
    it holds another 28 chemical_biological behaviours, which doubles the seed
    pool for the domain where seeds are scarcest -- and seeds, not framings,
    are what set the effective sample size.
    """
    url = (
        "https://raw.githubusercontent.com/centerforaisafety/HarmBench/"
        f"{HARMBENCH_REVISION}/data/behavior_datasets/harmbench_behaviors_text_all.csv"
    )
    rows = list(csv.DictReader(io.StringIO(_get(url).decode("utf-8"))))
    out: list[dict] = []
    for row in rows:
        if row["SemanticCategory"] != category:
            continue
        track = row["FunctionalCategory"]
        if track == "standard":
            out.append({"prompt": row["Behavior"], "category": category,
                        "source": "harmbench/standard"})
        elif track == "contextual" and row.get("ContextString"):
            out.append({
                "prompt": row["ContextString"].strip() + chr(10) * 2 + row["Behavior"],
                "category": category,
                "source": "harmbench/contextual",
            })
    return out


def _download_harmbench_bio() -> list[dict]:
    behaviours = _harmbench_slice(HARMBENCH_BIO_CATEGORY)
    if len(behaviours) != HARMBENCH_BIO_UPSTREAM:
        raise RuntimeError(
            f"HarmBench/{HARMBENCH_BIO_CATEGORY}: expected "
            f"{HARMBENCH_BIO_UPSTREAM} behaviours, got {len(behaviours)}."
        )
    return behaviours


def _download_swebench() -> list[dict]:
    """SWE-bench Verified: real issues with the gold patch that closed them."""
    rows: list[dict] = []
    offset = 0
    while True:
        url = (
            f"{_VIEWER}?dataset=princeton-nlp%2FSWE-bench_Verified&config=default"
            f"&split=test&offset={offset}&length={_PAGE}"
        )
        page = json.loads(_get(url))
        batch = page.get("rows", [])
        if not batch:
            break
        for entry in batch:
            row = entry["row"]
            rows.append({
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "problem_statement": row["problem_statement"],
                "patch": row["patch"],
                "difficulty": row.get("difficulty") or "unknown",
            })
        offset += len(batch)
        if offset >= page.get("num_rows_total", offset):
            break
    if len(rows) != SWEBENCH_UPSTREAM:
        raise RuntimeError(
            f"SWE-bench Verified: expected {SWEBENCH_UPSTREAM} instances, got {len(rows)}"
        )
    return rows


def fetch_swebench() -> list[dict]:
    return _cached("swebench-verified", _download_swebench)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n... (truncated)"


# Mutations applied to the *added* lines of a patch. Each flips one decision a
# correct fix had to get right, so a distractor addresses the same issue in the
# same file and is wrong only in its logic.
#
# Order matters: the first applicable operators are tried first, so items tend
# to differ by the sharpest change available rather than a cosmetic one.
_MUTATIONS: tuple[tuple[str, str, str], ...] = (
    ("comparison", "==", "!="),
    ("comparison", "!=", "=="),
    ("comparison", ">=", ">"),
    ("comparison", "<=", "<"),
    ("boundary", " < ", " <= "),
    ("boundary", " > ", " >= "),
    ("boolean", "True", "False"),
    ("boolean", "False", "True"),
    ("logical", " and ", " or "),
    ("logical", " or ", " and "),
    ("negation", "if not ", "if "),
    ("negation", "is not None", "is None"),
    ("negation", "is None", "is not None"),
)


def _added_lines(patch: str) -> list[int]:
    """Indices of lines the patch adds. Context and headers are left alone --
    mutating those would make the diff inconsistent rather than incorrect."""
    return [
        i
        for i, line in enumerate(patch.splitlines())
        if line.startswith("+") and not line.startswith("+++")
    ]


def _mutate(patch: str, find: str, replace: str) -> str | None:
    """Apply one operator to the first added line that admits it."""
    lines = patch.splitlines()
    for i in _added_lines(patch):
        if find in lines[i][1:]:
            lines[i] = "+" + lines[i][1:].replace(find, replace, 1)
            return chr(10).join(lines)
    return None


def _off_by_one(patch: str) -> str | None:
    """Shift the first integer literal on an added line."""
    lines = patch.splitlines()
    for i in _added_lines(patch):
        found = re.search(r"(?<![\w.])(\d+)(?![\w.])", lines[i][1:])
        if found:
            body = lines[i][1:]
            lines[i] = "+" + body[: found.start()] + str(int(found.group(1)) + 1) + body[found.end():]
            return chr(10).join(lines)
    return None


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_KEYWORDS = frozenset(
    "def class return import from self None True False and or not if elif else "
    "for while try except finally with as pass raise yield lambda assert del "
    "global nonlocal in is print len str int float list dict set tuple".split()
)


def _swap_identifier(patch: str) -> str | None:
    """Exchange two identifiers the patch adds.

    Applies to almost any real patch, keeps the diff the same length, and
    almost always breaks it -- a fix that reads the wrong variable is wrong in
    the way a fix is usually wrong.
    """
    lines = patch.splitlines()
    added = _added_lines(patch)
    names: list[str] = []
    for i in added:
        for name in _IDENT.findall(lines[i][1:]):
            if name not in _KEYWORDS and name not in names:
                names.append(name)
    if len(names) < 2:
        return None
    first, second = names[0], names[1]
    out = list(lines)
    swapped = False
    for i in added:
        body = out[i][1:]
        if first in body:
            out[i] = "+" + body.replace(first, second, 1)
            swapped = True
            break
    return chr(10).join(out) if swapped else None


def _swap_added_lines(patch: str) -> str | None:
    """Exchange two adjacent added lines. Order is load-bearing in most fixes."""
    lines = patch.splitlines()
    added = [i for i in _added_lines(patch) if lines[i][1:].strip()]
    for a, b in zip(added, added[1:]):
        if b == a + 1 and lines[a] != lines[b]:
            lines[a], lines[b] = lines[b], lines[a]
            return chr(10).join(lines)
    return None


def _shift_indent(patch: str) -> str | None:
    """Move an added line one level out. In Python that changes what the line
    belongs to, which is a real and common way to get a fix wrong."""
    lines = patch.splitlines()
    for i in _added_lines(patch):
        body = lines[i][1:]
        if body.startswith("        ") and body.strip():
            lines[i] = "+" + body[4:]
            return chr(10).join(lines)
    return None


_STRUCTURAL = (_swap_identifier, _swap_added_lines, _shift_indent, _off_by_one)


def mutants(patch: str, wanted: int = 3) -> list[str]:
    """Wrong versions of a correct patch, distinct from it and each other.

    Token operators run first because they flip a decision the fix had to get
    right, which is the sharpest kind of wrong. Structural operators follow and
    apply far more widely -- without them only 7% of SWE-bench patches admitted
    three distinct mutations, which is not enough to build a set from.

    Composition is the last resort: an operator applied to an already-mutated
    candidate. It keeps yield up on patches that admit only one change.
    """
    out: list[str] = []
    seen = {patch}

    def offer(candidate: str | None) -> bool:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
        return len(out) >= wanted

    for _, find, replace in _MUTATIONS:
        if offer(_mutate(patch, find, replace)):
            return out
    for operator in _STRUCTURAL:
        if offer(operator(patch)):
            return out
    for base in list(out):
        for operator in _STRUCTURAL:
            if offer(operator(base)):
                return out
    return out


def swebench_questions(rows: list[dict]) -> list[dict]:
    """Turn instances into four-way questions: which diff correctly fixes this?

    All four options are the *same* patch to the *same* file for the *same*
    issue; three have one decision flipped. That is the point.

    An earlier version drew distractors from other issues in the same
    repository, and the pilot showed what that measured. Qwen2.5-7B scored 92%,
    because a wrong option addresses a different problem and can be eliminated
    by noticing which files and symbols the issue mentions. The task was topic
    matching, not patch comprehension, and it inflated the capability estimate
    that sets the coding safety bar.

    Correctness is still inherited: the unmutated patch is the answer, because
    SWE-bench says it closed the issue. Nothing here decides what is correct.

    Instances whose patch admits fewer than three distinct mutations are
    dropped. Falling back to another issue's patch would reintroduce exactly
    the shortcut this removes, for the items where it bites hardest.
    """
    items: list[dict] = []
    for row in rows:
        gold = _clip(row["patch"], _PATCH_CHARS)
        wrong = mutants(gold)
        if len(wrong) < 3:
            continue
        options = [gold, *wrong]
        order = sorted(range(4), key=lambda i: _hash_key(options[i], row["instance_id"]))
        items.append({
            "question": (
                f"Repository: {row['repo']}" + chr(10) * 2 + "Issue:" + chr(10)
                + _clip(row["problem_statement"], _STATEMENT_CHARS)
            ),
            "choices": [options[i] for i in order],
            "answer": order.index(0),
            "difficulty": row["difficulty"],
            "instance_id": row["instance_id"],
        })
    return items


def _hash_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def fetch_harmbench_coding() -> list[dict]:
    return _cached(f"harmbench-{HARMBENCH_CODING_CATEGORY}", _download_harmbench_coding)


def _download_advbench_coding() -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(_get(ADVBENCH_URL).decode("utf-8"))))
    out = []
    for row in rows:
        goal = row["goal"]
        if re.search(_CODING_PATTERN, goal, re.I) and not re.search(
            _BIO_PATTERN, goal, re.I
        ):
            out.append({"prompt": goal, "category": HARMBENCH_CODING_CATEGORY,
                        "source": "advbench"})
    return out


def _download_strongreject_legal() -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(_get(STRONGREJECT_URL).decode("utf-8"))))
    return [
        {"prompt": row["forbidden_prompt"], "category": row["category"],
         "source": "strongreject"}
        for row in rows
        if row["category"] in _STRONGREJECT_LEGAL
    ]


def _download_harmbench_coding() -> list[dict]:
    behaviours = _harmbench_slice(HARMBENCH_CODING_CATEGORY)
    behaviours += _download_advbench_coding()
    if len(behaviours) != HARMBENCH_CODING_UPSTREAM:
        raise RuntimeError(
            f"HarmBench/{HARMBENCH_CODING_CATEGORY}: expected "
            f"{HARMBENCH_CODING_UPSTREAM} behaviours, got {len(behaviours)}"
        )
    return behaviours


def _download_legalbench() -> list[dict]:
    """Binary legal-reasoning questions, pooled across several LegalBench tasks.

    Pooled rather than drawn from one task so the probe measures legal
    reasoning broadly instead of one narrow skill -- a model good at consumer
    contracts and hopeless at citation validity should not read as uniformly
    capable.
    """
    items: list[dict] = []
    for task, context_key, question_key in LEGALBENCH_TASKS:
        offset = 0
        while True:
            url = (
                f"{_VIEWER}?dataset=nguha%2Flegalbench&config={task}"
                f"&split=test&offset={offset}&length={_PAGE}"
            )
            page = json.loads(_get(url))
            batch = page.get("rows", [])
            if not batch:
                break
            for entry in batch:
                row = entry["row"]
                answer = str(row.get("answer", "")).strip()
                if answer not in ("Yes", "No"):
                    continue  # non-binary task rows are dropped, not coerced
                items.append({
                    "task": task,
                    "context": str(row.get(context_key) or ""),
                    "query": str(row.get(question_key) or ""),
                    "answer_text": answer,
                })
            offset += len(batch)
            if offset >= page.get("num_rows_total", offset):
                break
    return items


def fetch_legalbench() -> list[dict]:
    return _cached("legalbench", _download_legalbench)


def legalbench_questions(rows: list[dict]) -> list[dict]:
    """Yes/No items in the shared MCQ shape."""
    out: list[dict] = []
    for row in rows:
        context = _clip(row["context"], _STATEMENT_CHARS)
        query = row["query"].strip()
        stem = f"{context}" + (chr(10) * 2) + f"{query}" if context else query
        out.append({
            "question": stem,
            "choices": ["Yes", "No"],
            "answer": 0 if row["answer_text"] == "Yes" else 1,
            "task": row["task"],
        })
    return out


def fetch_harmbench_legal() -> list[dict]:
    return _cached(f"harmbench-{HARMBENCH_LEGAL_CATEGORY}", _download_harmbench_legal)


def _download_harmbench_legal() -> list[dict]:
    behaviours = _harmbench_slice(HARMBENCH_LEGAL_CATEGORY)
    behaviours += _download_strongreject_legal()
    if len(behaviours) != HARMBENCH_LEGAL_UPSTREAM:
        raise RuntimeError(
            f"HarmBench/{HARMBENCH_LEGAL_CATEGORY}: expected "
            f"{HARMBENCH_LEGAL_UPSTREAM} behaviours, got {len(behaviours)}"
        )
    return behaviours


def _download_general() -> list[dict]:
    """Every harm with no capability axis, from all three corpora."""
    out: list[dict] = []
    for category in sorted(GENERAL_CATEGORIES):
        out += [
            {**row, "source": "harmbench"} for row in _harmbench_slice(category)
        ]

    page = json.loads(_get(JBB_URL))
    out += [
        {
            "prompt": entry["row"]["Goal"],
            "category": entry["row"].get("Category", "jailbreakbench"),
            "source": "jailbreakbench",
        }
        for entry in page.get("rows", [])
    ]

    rows = list(csv.DictReader(io.StringIO(_get(STRONGREJECT_URL).decode("utf-8"))))
    out += [
        {"prompt": row["forbidden_prompt"], "category": row["category"],
         "source": "strongreject"}
        for row in rows
        if row["category"] not in _STRONGREJECT_LEGAL
    ]
    return out


def fetch_general() -> list[dict]:
    return _cached("general", _download_general)


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
    if suite_id == "legal_elicitation":
        return len(expand(pool, LEGAL_TRANSFORMS, variants=LEGAL_FRAMING_COUNT))
    if suite_id.endswith("_elicitation"):
        return len(expand(pool, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT))
    if suite_id == "coding_probe":
        return len(swebench_questions(pool))
    if suite_id == "legal_probe":
        return len(legalbench_questions(pool))
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


def build_coding_probe_set(pool: list[dict], rotation: int) -> list[dict]:
    """Questions first, then rotate. Order matters.

    Building every question up front and drawing from that gives a pool of ~500
    rather than the 100 a pre-rotation draw would leave, so consecutive
    rotations share almost nothing. Rotating first would have capped the pool
    at the draw size and forced reuse for no reason.
    """
    return rotate(swebench_questions(pool), SET_SIZE, rotation)


def build_legal_probe_set(pool: list[dict], rotation: int) -> list[dict]:
    return rotate(legalbench_questions(pool), SET_SIZE, rotation)


def build_legal_elicitation_set(pool: list[dict], rotation: int) -> list[dict]:
    expanded = expand(pool, LEGAL_TRANSFORMS, variants=LEGAL_FRAMING_COUNT)
    return rotate(expanded, ELICITATION_SET_SIZE, rotation)


def build_elicitation_set(pool: list[dict], rotation: int) -> list[dict]:
    """Expand, because the pool is smaller than the target.

    28 behaviours against a target of 100. Without expansion the strictest band
    is cleared only by a perfect run, so the published set is not gateable at
    its own size -- expansion is what makes the bar mean anything.
    """
    expanded = expand(pool, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    return rotate(expanded, ELICITATION_SET_SIZE, rotation)


_SETS = (
    ("bio_probe", "WMDP-bio", fetch_wmdp_bio, build_probe_set, WMDP_BIO_DIGEST),
    (
        "bio_elicitation",
        f"HarmBench/{HARMBENCH_BIO_CATEGORY}",
        fetch_harmbench_bio,
        build_elicitation_set,
        HARMBENCH_BIO_DIGEST,
    ),
    (
        "coding_probe",
        "SWE-bench Verified",
        fetch_swebench,
        build_coding_probe_set,
        SWEBENCH_DIGEST,
    ),
    (
        "coding_elicitation",
        f"HarmBench/{HARMBENCH_CODING_CATEGORY}",
        fetch_harmbench_coding,
        build_elicitation_set,
        HARMBENCH_CODING_DIGEST,
    ),
    (
        "legal_probe",
        "LegalBench",
        fetch_legalbench,
        build_legal_probe_set,
        LEGALBENCH_DIGEST,
    ),
    (
        "general_elicitation",
        "fluency harms, all sources",
        fetch_general,
        build_elicitation_set,
        GENERAL_DIGEST,
    ),
    (
        "legal_elicitation",
        f"HarmBench/{HARMBENCH_LEGAL_CATEGORY}",
        fetch_harmbench_legal,
        build_legal_elicitation_set,
        HARMBENCH_LEGAL_DIGEST,
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
        elicitation = suite_id.endswith("_elicitation")
        target = ELICITATION_SET_SIZE if elicitation else SET_SIZE
        # Freshness is measured at the budget a run actually draws, not at the
        # staged size. The whole pool is staged and rotation orders it; a suite
        # takes a prefix, so two rotations differ in what lands in that prefix.
        # Reporting reuse at the full staged size would read 100% and mean
        # nothing.
        drawn = (
            items_for_deficit(
                allowed_deficit(0.55), TYPICAL_BASELINE_RATE, TYPICAL_BASELINE_N
            )
            if elicitation
            else len(items)
        )
        staged[suite_id] = {
            "source": name,
            "pool": len(pool),
            "variants": variants,
            "n": len(items),
            "digest": found,
            "short_by": max(0, target - len(items)),
            "overlap_floor": round(100 * overlap_floor(variants, drawn) / max(drawn, 1)),
            "drawn": drawn,
        }

    return staged
