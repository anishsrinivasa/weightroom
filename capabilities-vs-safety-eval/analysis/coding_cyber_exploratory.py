#!/usr/bin/env python3
"""Build the exploratory EvalPlus → cyber-safeguard overlap panel.

The analysis joins exact checkpoint labels from EvalPlus and Safetywashing.
It intentionally does not fuzzy-match model names. The resulting eight-model
panel is below the preregistered confirmatory threshold and is therefore a
coverage diagnostic, not a release classifier.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import random
import statistics
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "outputs"
FIGURE_DIR = OUTPUT_DIR / "figures"

EVALPLUS_COMMIT = "87418456d3a8c2bc6265f24ff91d2dd0e7993f4b"
SAFETYWASHING_COMMIT = "bee302c11f174afb064d721123b16b810d34f4fd"
EVALPLUS_URL = (
    "https://raw.githubusercontent.com/evalplus/evalplus.github.io/"
    f"{EVALPLUS_COMMIT}/results.json"
)
SAFETYWASHING_URL = (
    "https://raw.githubusercontent.com/centerforaisafety/safetywashing/"
    f"{SAFETYWASHING_COMMIT}/data/benchmarks_chat_models.csv"
)
EVALPLUS_SHA256 = "e6837dc3b94f32cb1da0ced012e52edc4a722007b684df5b63da029bbd3b46d2"
SAFETYWASHING_SHA256 = "5ede988872d8bba23d0bb87fcebb9704d7fe7d4e6f065226b691499333f57c58"
EVALPLUS_FILE = RAW_DIR / "evalplus_results.json"
SAFETYWASHING_FILE = RAW_DIR / "safetywashing_chat_models.csv"

BOOTSTRAP_REPLICATES = 5_000
BOOTSTRAP_SEED = 20260905


# Deliberately audited aliases only. Similar-looking versions are not accepted.
EXACT_MODEL_MAP = {
    "Meta-Llama-3-70B-Instruct": "Llama3-70B-instruct",
    "Meta-Llama-3-8B-Instruct": "Llama3-8B-instruct",
    "Mistral-7B-Instruct-v0.2": "Mistral-7B-Instruct-v0.2",
    "Mixtral-8x22B-Instruct-v0.1": "Mixtral-8x22B-Instruct-v0.1",
    "Mixtral-8x7B-Instruct-v0.1": "Mixtral-8x7B-Instruct-v0.1",
    "Qwen1.5-72B-Chat": "Qwen1.5-72B-Chat",
    "gemma-1.1-2b-it": "gemma-1.1-2b-it",
    "gemma-1.1-7b-it": "gemma-1.1-7b-it",
}

FAMILY = {
    "Meta-Llama-3-70B-Instruct": "Meta Llama 3",
    "Meta-Llama-3-8B-Instruct": "Meta Llama 3",
    "Mistral-7B-Instruct-v0.2": "Mistral/Mixtral",
    "Mixtral-8x22B-Instruct-v0.1": "Mistral/Mixtral",
    "Mixtral-8x7B-Instruct-v0.1": "Mistral/Mixtral",
    "Qwen1.5-72B-Chat": "Qwen 1.5",
    "gemma-1.1-2b-it": "Gemma 1.1",
    "gemma-1.1-7b-it": "Gemma 1.1",
}


@dataclass(frozen=True)
class SafetyMetric:
    column: str
    label: str
    construct: str
    source_scale: str
    direction: str


BEHAVIORAL_METRICS = (
    SafetyMetric(
        "HarmBench-DirectRequest-cybercrime_intrusion",
        "HarmBench cyber direct-request attack failure",
        "harmful-compliance resistance",
        "percent",
        "higher_is_safer",
    ),
    SafetyMetric(
        "HarmBench-HumanJailbreaks-cybercrime_intrusion",
        "HarmBench cyber human-jailbreak attack failure",
        "human-jailbreak resistance",
        "percent",
        "higher_is_safer",
    ),
    SafetyMetric(
        "HarmBench-TAP-T-cybercrime_intrusion",
        "HarmBench cyber TAP-T attack failure",
        "automated-jailbreak resistance",
        "percent",
        "higher_is_safer",
    ),
    SafetyMetric(
        "HarmBench-GCG-T-cybercrime_intrusion",
        "HarmBench cyber GCG-T attack failure",
        "adversarial-suffix resistance",
        "percent",
        "higher_is_safer",
    ),
    SafetyMetric(
        "cyberseceval2_instruct",
        "CyberSecEval 2 instruct safe-suggestion rate",
        "secure code instruction behavior",
        "percent",
        "higher_is_safer",
    ),
    SafetyMetric(
        "cyberseceval2_mitre",
        "CyberSecEval 2 MITRE safe-response rate",
        "offensive-cyber harmful-compliance resistance",
        "proportion",
        "higher_is_safer",
    ),
    SafetyMetric(
        "cyberseceval2_prompt_injection",
        "CyberSecEval 2 prompt-injection attack failure",
        "prompt-injection resistance",
        "proportion",
        "higher_is_safer",
    ),
    SafetyMetric(
        "cyberseceval2_autocomplete",
        "CyberSecEval 2 autocomplete safe-suggestion rate",
        "secure code completion behavior",
        "percent",
        "higher_is_safer",
    ),
)

NONBEHAVIORAL_METRICS = (
    SafetyMetric(
        "cyberseceval2_canary_exploit",
        "CyberSecEval 2 vulnerability detection rate",
        "dual-use vulnerability-detection capability; not a safeguard score",
        "proportion",
        "higher_is_better_source_metric",
    ),
    SafetyMetric(
        "wmdp_cyber",
        "WMDP-Cyber inverted accuracy",
        "lack of hazardous cyber knowledge; Safetywashing stores an inverted score",
        "proportion",
        "higher_is_safer",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path, expected_sha256: str) -> None:
    if path.exists() and sha256(path) == expected_sha256:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    request = urllib.request.Request(url, headers={"User-Agent": "keystone-research/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
        out.write(response.read())
    actual = sha256(temporary)
    if actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"digest mismatch for {url}: expected {expected_sha256}, got {actual}")
    temporary.replace(path)


def acquire_sources() -> None:
    download(EVALPLUS_URL, EVALPLUS_FILE, EVALPLUS_SHA256)
    download(SAFETYWASHING_URL, SAFETYWASHING_FILE, SAFETYWASHING_SHA256)


def to_percent(value: str, source_scale: str) -> float:
    parsed = float(value)
    return parsed * 100 if source_scale == "proportion" else parsed


def load_panel() -> list[dict[str, object]]:
    evalplus = json.loads(EVALPLUS_FILE.read_text(encoding="utf-8"))
    with SAFETYWASHING_FILE.open(newline="", encoding="utf-8") as handle:
        safety_rows = {row["model"]: row for row in csv.DictReader(handle)}

    panel: list[dict[str, object]] = []
    for safety_model, capability_model in EXACT_MODEL_MAP.items():
        if safety_model not in safety_rows or capability_model not in evalplus:
            raise RuntimeError(f"audited model mapping no longer resolves: {safety_model}")
        safety = safety_rows[safety_model]
        pass_at_1 = evalplus[capability_model]["pass@1"]
        row: dict[str, object] = {
            "model": safety_model,
            "evalplus_model": capability_model,
            "family": FAMILY[safety_model],
            "humaneval_plus": float(pass_at_1["humaneval+"]),
            "mbpp_plus": float(pass_at_1["mbpp+"]),
        }
        row["code_capability"] = statistics.mean(
            [float(row["humaneval_plus"]), float(row["mbpp_plus"])]
        )
        for metric in BEHAVIORAL_METRICS + NONBEHAVIORAL_METRICS:
            row[metric.column] = to_percent(safety[metric.column], metric.source_scale)
        row["behavioral_safety_composite"] = statistics.mean(
            float(row[metric.column]) for metric in BEHAVIORAL_METRICS
        )
        panel.append(row)
    return panel


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return math.nan
    mx, my = mean(xs), mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else math.nan


def ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2
        for position in range(cursor, end):
            result[ordered[position][0]] = average_rank
        cursor = end
    return result


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    return pearson(ranks(xs), ranks(ys))


def percentile(values: Sequence[float], probability: float) -> float:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return math.nan
    position = (len(finite) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return finite[low]
    weight = position - low
    return finite[low] * (1 - weight) + finite[high] * weight


def family_bootstrap(panel: Sequence[dict[str, object]], y_column: str, seed: int) -> tuple[float, float]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in panel:
        groups.setdefault(str(row["family"]), []).append(row)
    families = sorted(groups)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample: list[dict[str, object]] = []
        for _ in families:
            sample.extend(groups[rng.choice(families)])
        estimate = spearman(
            [float(row["code_capability"]) for row in sample],
            [float(row[y_column]) for row in sample],
        )
        if math.isfinite(estimate):
            estimates.append(estimate)
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    mx, my = mean(xs), mean(ys)
    denominator = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denominator
    return slope, my - slope * mx


def residual_scale(residuals: Sequence[float]) -> float:
    center = statistics.median(residuals)
    mad = statistics.median(abs(value - center) for value in residuals)
    scale = 1.4826 * mad
    return scale if scale > 1e-12 else math.sqrt(mean([value * value for value in residuals]))


def fmt(value: float, digits: int = 3) -> str:
    return "NA" if not math.isfinite(value) else f"{value:.{digits}f}"


def write_csv(path: Path, rows: Iterable[dict[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_svg(panel: Sequence[dict[str, object]], slope: float, intercept: float) -> None:
    width, height = 820, 540
    left, right, top, bottom = 86, 28, 64, 78
    plot_width, plot_height = width - left - right, height - top - bottom
    xs = [float(row["code_capability"]) for row in panel]
    ys = [float(row["behavioral_safety_composite"]) for row in panel]
    x0, x1 = min(xs) - 4, max(xs) + 4
    y0, y1 = min(ys) - 5, max(ys) + 5

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0) * plot_width

    def sy(value: float) -> float:
        return top + (y1 - value) / (y1 - y0) * plot_height

    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">EvalPlus coding capability versus cyber behavioral safety</title>',
        '<desc id="desc">Eight exact open-weight chat checkpoints with a descriptive ordinary least squares trend.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="29" font-family="system-ui,sans-serif" font-size="18" font-weight="600" fill="#111827">Coding capability vs. cyber behavioral safety</text>',
        f'<text x="{left}" y="49" font-family="system-ui,sans-serif" font-size="12" fill="#4b5563">Exact cross-source overlap · n=8 · exploratory only; dashed line is not a release threshold</text>',
        f'<defs><clipPath id="coding-cyber-clip"><rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}"/></clipPath></defs>',
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#ffffff" stroke="#9ca3af"/>',
    ]
    for tick in range(6):
        xv = x0 + (x1 - x0) * tick / 5
        yv = y0 + (y1 - y0) * tick / 5
        xp, yp = sx(xv), sy(yv)
        pieces.extend(
            [
                f'<line x1="{xp:.1f}" y1="{top}" x2="{xp:.1f}" y2="{top + plot_height}" stroke="#e5e7eb"/>',
                f'<text x="{xp:.1f}" y="{top + plot_height + 22}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{xv:.0f}</text>',
                f'<line x1="{left}" y1="{yp:.1f}" x2="{left + plot_width}" y2="{yp:.1f}" stroke="#e5e7eb"/>',
                f'<text x="{left - 10}" y="{yp + 4:.1f}" text-anchor="end" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{yv:.0f}</text>',
            ]
        )
    pieces.append(
        f'<line x1="{sx(x0):.1f}" y1="{sy(slope * x0 + intercept):.1f}" x2="{sx(x1):.1f}" y2="{sy(slope * x1 + intercept):.1f}" stroke="#111827" stroke-width="2" stroke-dasharray="7 5" clip-path="url(#coding-cyber-clip)"/>'
    )
    colors = {
        "Meta Llama 3": "#2563eb",
        "Mistral/Mixtral": "#dc2626",
        "Qwen 1.5": "#7c3aed",
        "Gemma 1.1": "#059669",
    }
    for row in panel:
        x, y = float(row["code_capability"]), float(row["behavioral_safety_composite"])
        color = colors[str(row["family"])]
        label = str(row["model"]).replace("-Instruct", "").replace("-Chat", "")
        label_on_left = sx(x) > left + plot_width - 175
        label_x = sx(x) - 7 if label_on_left else sx(x) + 7
        label_anchor = "end" if label_on_left else "start"
        pieces.extend(
            [
                f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="5" fill="{color}" stroke="#ffffff"><title>{html.escape(str(row["model"]))}: coding={x:.1f}, safety={y:.1f}</title></circle>',
                f'<text x="{label_x:.1f}" y="{sy(y) - 7:.1f}" text-anchor="{label_anchor}" font-family="system-ui,sans-serif" font-size="10" fill="#374151">{html.escape(label)}</text>',
            ]
        )
    pieces.extend(
        [
            f'<text x="{left + plot_width / 2}" y="{height - 24}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111827">EvalPlus mean of HumanEval+ and MBPP+ pass@1 (%)</text>',
            f'<text x="20" y="{top + plot_height / 2}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2})" font-family="system-ui,sans-serif" font-size="12" fill="#111827">Cyber behavioral-safety composite (%)</text>',
            '</svg>',
        ]
    )
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIGURE_DIR / "coding_capability__cyber_behavioral_safety.svg").write_text(
        "\n".join(pieces) + "\n", encoding="utf-8"
    )


def make_report(
    relationships: Sequence[dict[str, object]], residuals: Sequence[dict[str, object]]
) -> str:
    composite = next(row for row in relationships if row["metric"] == "behavioral_safety_composite")
    ordered = sorted(residuals, key=lambda row: float(row["standardized_gap"]))
    lines = [
        "# Exploratory Coding→Cyber Capability–Safety Panel",
        "",
        "**Status:** exploratory coverage diagnostic; not a release decision",
        f"**Capability source:** EvalPlus at `{EVALPLUS_COMMIT}`",
        f"**Safety source:** Safetywashing at `{SAFETYWASHING_COMMIT}`",
        "**Exact overlap:** 8 instruction/chat checkpoints across 4 model families",
        "",
        "## Main finding",
        "",
        f"The descriptive association between code correctness and the cyber behavioral-safety composite is weakly negative (Spearman ρ={composite['spearman_rho']}; Pearson r={composite['pearson_r']}). The family-cluster bootstrap interval is [{composite['spearman_ci_low']}, {composite['spearman_ci_high']}], which is too wide for confirmatory inference.",
        "",
        "The plot is more informative as a lineage diagnostic: all three Mistral/Mixtral checkpoints fall below the pooled trend, while both Meta Llama 3 and both Gemma 1.1 checkpoints fall above it. With only four families, lineage/alignment choices are inseparable from model capability. The pooled slope must not be used as a release boundary.",
        "",
        "## Relationship estimates",
        "",
        "All behavioral scores are oriented so higher means safer. WMDP is already stored as inverted accuracy (higher means less hazardous knowledge). The vulnerability-detection metric is dual-use capability evidence, not a safeguard score. Neither nonbehavioral metric is included in the behavioral-safety composite.",
        "",
        "| Outcome | Construct | Direction | n | Spearman ρ | 95% family-cluster interval | Pearson r |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in relationships:
        lines.append(
            f"| {row['label']} | {row['construct']} | {row['direction']} | {row['n_models']} | {row['spearman_rho']} | [{row['spearman_ci_low']}, {row['spearman_ci_high']}] | {row['pearson_r']} |"
        )
    lines.extend(
        [
            "",
            "## Composite residual audit",
            "",
            "Negative gaps indicate lower behavioral safeguards than the pooled eight-model trend predicts. These are triage signals only.",
            "",
            "| Model | Family | Code capability | Observed safety | Expected safety | Gap | Robust standardized gap |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in ordered:
        lines.append(
            f"| `{row['model']}` | {row['family']} | {float(row['code_capability']):.1f} | {float(row['observed_safety']):.1f} | {float(row['expected_safety']):.1f} | {float(row['safety_gap']):+.1f} | {float(row['standardized_gap']):+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Why this cannot yet answer the release question",
            "",
            "- The panel has fewer than the planned 12 artifacts and only four lineages.",
            "- EvalPlus uses short Python synthesis tasks; it is not equivalent to repository-level autonomous software engineering.",
            "- The safety composite averages heterogeneous constructs for visualization only. A release rule must preserve per-test floors.",
            "- Public benchmark aggregates do not expose item-level covariance or uncertainty here.",
            "- Open weights permit removal or replacement of the tested chat template and alignment behavior.",
            "- SWE-bench scores depend on the agent scaffold, tools, attempts, and budget. They cannot be joined to bare-model safety scores without rerunning a fixed profile.",
            "",
            "## Required next measurement",
            "",
            "Run a registered panel of at least 12 exact open-weight checkpoints from at least four independent lineages under one inference stack. Measure LiveCodeBench/BigCodeBench, a fixed SWE-bench agent profile, CyberSecEval secure-code and harmful-compliance tracks, Cybench under bounded strong elicitation, benign cyber utility, and post-modification safety. Only then fit a candidate minimum envelope.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    acquire_sources()
    panel = load_panel()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    metric_specs = list(BEHAVIORAL_METRICS) + list(NONBEHAVIORAL_METRICS)
    metric_specs.insert(
        0,
        SafetyMetric(
            "behavioral_safety_composite",
            "Cyber behavioral-safety composite",
            "descriptive mean of eight behavioral safeguard metrics",
            "percent",
            "higher_is_safer",
        ),
    )
    relationships: list[dict[str, object]] = []
    for index, metric in enumerate(metric_specs):
        xs = [float(row["code_capability"]) for row in panel]
        ys = [float(row[metric.column]) for row in panel]
        ci_low, ci_high = family_bootstrap(panel, metric.column, BOOTSTRAP_SEED + index)
        relationships.append(
            {
                "domain": "software_engineering_to_cyber",
                "capability_metric": "mean_humaneval_plus_mbpp_plus_pass_at_1",
                "metric": metric.column,
                "label": metric.label,
                "construct": metric.construct,
                "direction": metric.direction,
                "n_models": len(panel),
                "n_families": len({str(row["family"]) for row in panel}),
                "pearson_r": fmt(pearson(xs, ys)),
                "spearman_rho": fmt(spearman(xs, ys)),
                "spearman_ci_low": fmt(ci_low),
                "spearman_ci_high": fmt(ci_high),
            }
        )

    xs = [float(row["code_capability"]) for row in panel]
    ys = [float(row["behavioral_safety_composite"]) for row in panel]
    slope, intercept = linear_fit(xs, ys)
    raw_residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    scale = residual_scale(raw_residuals)
    residuals: list[dict[str, object]] = []
    for row, residual in zip(panel, raw_residuals):
        residuals.append(
            {
                "model": row["model"],
                "family": row["family"],
                "code_capability": f"{float(row['code_capability']):.6f}",
                "observed_safety": f"{float(row['behavioral_safety_composite']):.6f}",
                "expected_safety": f"{slope * float(row['code_capability']) + intercept:.6f}",
                "safety_gap": f"{residual:.6f}",
                "standardized_gap": f"{residual / scale:.6f}",
            }
        )

    panel_fields = [
        "model",
        "evalplus_model",
        "family",
        "humaneval_plus",
        "mbpp_plus",
        "code_capability",
        "behavioral_safety_composite",
    ] + [metric.column for metric in BEHAVIORAL_METRICS + NONBEHAVIORAL_METRICS]
    write_csv(OUTPUT_DIR / "coding_cyber_exploratory_panel.csv", panel, panel_fields)
    write_csv(
        OUTPUT_DIR / "coding_cyber_exploratory_relationships.csv",
        relationships,
        list(relationships[0]),
    )
    write_csv(
        OUTPUT_DIR / "coding_cyber_exploratory_residuals.csv",
        residuals,
        list(residuals[0]),
    )
    write_svg(panel, slope, intercept)
    (OUTPUT_DIR / "coding_cyber_exploratory_report.md").write_text(
        make_report(relationships, residuals), encoding="utf-8"
    )
    print(f"Wrote coding→cyber exploratory outputs for {len(panel)} exact checkpoints.")


if __name__ == "__main__":
    main()
