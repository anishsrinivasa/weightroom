#!/usr/bin/env python3
"""Reproduce the preliminary BenchMIRT capability-safety analysis.

This script intentionally uses only the Python standard library. It downloads a
commit-pinned source file, verifies its digest, computes descriptive
relationships, ranks residuals, and writes compact SVG figures and a Markdown
report. The residuals are comparative diagnostics, not release verdicts.
"""

from __future__ import annotations

import csv
import hashlib
import html
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

SOURCE_COMMIT = "8672cad1429df9e885f9651d3ae01621748662f5"
SOURCE_URL = (
    "https://huggingface.co/datasets/allenai/BenchMIRT-model-statistics/resolve/"
    f"{SOURCE_COMMIT}/llm_statistics.csv?download=true"
)
SOURCE_SHA256 = "9cff23cde83b954fed36c3990711c41f73dca6a342a4907a249bfa14ed214695"
RAW_FILE = RAW_DIR / "benchmirt_model_statistics.csv"

BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 20260905


@dataclass(frozen=True)
class Pair:
    pair_id: str
    domain: str
    x_column: str
    y_column: str
    x_label: str
    y_label: str
    construct: str


PAIRS = (
    Pair(
        "general_reasoning__selected_safety",
        "general_reasoning",
        "reasoning_average",
        "select_safety_average",
        "General reasoning average",
        "Selected behavioral safety average",
        "Broad behavioral safety excluding BBQ and WMDP",
    ),
    Pair(
        "general_reasoning__harmbench",
        "general_reasoning",
        "reasoning_average",
        "harmbench_average",
        "General reasoning average",
        "HarmBench safety score",
        "Resistance to harmful compliance",
    ),
    Pair(
        "general_reasoning__strongreject",
        "general_reasoning",
        "reasoning_average",
        "strongreject_average",
        "General reasoning average",
        "StrongREJECT safety score",
        "Resistance to harmful compliance",
    ),
    Pair(
        "general_reasoning__wildjailbreak",
        "general_reasoning",
        "reasoning_average",
        "wildjailbreak_average",
        "General reasoning average",
        "WildJailbreak safety score",
        "Resistance to in-the-wild jailbreaks",
    ),
    Pair(
        "general_reasoning__xstest",
        "general_reasoning",
        "reasoning_average",
        "xstest_average",
        "General reasoning average",
        "XSTest balanced safety score",
        "Unsafe-request refusal plus benign compliance",
    ),
    Pair(
        "general_reasoning__wmdp",
        "scientific_hazardous_knowledge_proxy",
        "reasoning_average",
        "wmdp_average",
        "General reasoning average",
        "WMDP safety-oriented score",
        "Lack of hazardous knowledge; BenchMIRT stores 1 - correct",
    ),
    Pair(
        "gpqa__wmdp",
        "scientific_hazardous_knowledge_proxy",
        "gpqa_average",
        "wmdp_average",
        "GPQA accuracy",
        "WMDP safety-oriented score",
        "Science reasoning versus lack of hazardous knowledge",
    ),
    Pair(
        "ifeval__harmbench",
        "instruction_following",
        "ifeval_average",
        "harmbench_average",
        "IFEval accuracy",
        "HarmBench safety score",
        "Instruction following versus harmful-compliance resistance",
    ),
    Pair(
        "ifeval__strongreject",
        "instruction_following",
        "ifeval_average",
        "strongreject_average",
        "IFEval accuracy",
        "StrongREJECT safety score",
        "Instruction following versus harmful-compliance resistance",
    ),
    Pair(
        "ifeval__wildjailbreak",
        "instruction_following",
        "ifeval_average",
        "wildjailbreak_average",
        "IFEval accuracy",
        "WildJailbreak safety score",
        "Instruction following versus jailbreak resistance",
    ),
    Pair(
        "ifeval__jailbreaktrigger",
        "instruction_following",
        "ifeval_average",
        "trustllm_jailbreaktrigger_average",
        "IFEval accuracy",
        "TrustLLM JailbreakTrigger safety score",
        "Instruction following versus jailbreak-trigger resistance",
    ),
    Pair(
        "ifeval__xstest",
        "instruction_following",
        "ifeval_average",
        "xstest_average",
        "IFEval accuracy",
        "XSTest balanced safety score",
        "Instruction following versus safe discrimination/over-refusal",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_source() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_FILE.exists() and sha256(RAW_FILE) == SOURCE_SHA256:
        return
    temp = RAW_FILE.with_suffix(".download")
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "keystone-research/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as output:
        output.write(response.read())
    actual = sha256(temp)
    if actual != SOURCE_SHA256:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"source digest mismatch: expected {SOURCE_SHA256}, got {actual}")
    temp.replace(RAW_FILE)


def load_rows() -> list[dict[str, str]]:
    with RAW_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def numeric_rows(rows: Sequence[dict[str, str]], pair: Pair) -> list[dict[str, object]]:
    usable: list[dict[str, object]] = []
    for row in rows:
        try:
            x = float(row[pair.x_column])
            y = float(row[pair.y_column])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        usable.append(
            {
                "model": row["model"],
                "family": row["family"],
                "model_class": row["reasoning"],
                "x": x,
                "y": y,
            }
        )
    return usable


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        return math.nan
    mx, my = mean(xs), mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx <= 0 or dy <= 0:
        return math.nan
    return numerator / math.sqrt(dx * dy)


def ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            result[indexed[position][0]] = average_rank
        cursor = end
    return result


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    return pearson(ranks(xs), ranks(ys))


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    mx, my = mean(xs), mean(ys)
    denominator = sum((x - mx) ** 2 for x in xs)
    if denominator <= 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denominator
    return slope, my - slope * mx


def percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(v for v in values if math.isfinite(v))
    if not ordered:
        return math.nan
    index = (len(ordered) - 1) * probability
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def cluster_bootstrap_correlation(
    rows: Sequence[dict[str, object]], seed: int
) -> tuple[float, float]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["family"]), []).append(row)
    families = sorted(grouped)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled: list[dict[str, object]] = []
        for _ in families:
            sampled.extend(grouped[rng.choice(families)])
        xs = [float(row["x"]) for row in sampled]
        ys = [float(row["y"]) for row in sampled]
        estimate = spearman(xs, ys)
        if math.isfinite(estimate):
            estimates.append(estimate)
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def residual_scale(residuals: Sequence[float]) -> float:
    center = statistics.median(residuals)
    mad = statistics.median(abs(value - center) for value in residuals)
    scale = 1.4826 * mad
    if scale > 1e-12:
        return scale
    rmse = math.sqrt(mean([value * value for value in residuals]))
    return rmse if rmse > 1e-12 else 1.0


def fmt(value: float, digits: int = 3) -> str:
    return "NA" if not math.isfinite(value) else f"{value:.{digits}f}"


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def padded_extent(values: Sequence[float]) -> tuple[float, float]:
    low, high = min(values), max(values)
    if low == high:
        return low - 0.05, high + 0.05
    padding = (high - low) * 0.07
    return low - padding, high + padding


def svg_scatter(
    pair: Pair,
    rows: Sequence[dict[str, object]],
    slope: float,
    intercept: float,
    label_models: set[str],
) -> str:
    width, height = 820, 540
    left, right, top, bottom = 84, 28, 62, 78
    plot_width = width - left - right
    plot_height = height - top - bottom
    xs = [float(row["x"]) for row in rows]
    ys = [float(row["y"]) for row in rows]
    x0, x1 = padded_extent(xs)
    y0, y1 = padded_extent(ys)

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0) * plot_width

    def sy(value: float) -> float:
        return top + (y1 - value) / (y1 - y0) * plot_height

    palette = {
        "Instruct": "#2563eb",
        "Reasoning": "#dc2626",
        "Base": "#7c3aed",
    }
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(pair.x_label)} versus {html.escape(pair.y_label)}</title>',
        f'<desc id="desc">Scatterplot of {len(rows)} open-weight model artifacts with an ordinary least squares descriptive trend.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="28" font-family="system-ui,sans-serif" font-size="18" font-weight="650" fill="#111827">{html.escape(pair.x_label)} vs. {html.escape(pair.y_label)}</text>',
        f'<text x="{left}" y="48" font-family="system-ui,sans-serif" font-size="12" fill="#4b5563">BenchMIRT panel · n={len(rows)} · descriptive OLS trend, not a release boundary</text>',
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#ffffff" stroke="#9ca3af" stroke-width="1"/>',
    ]

    for tick in range(6):
        x_value = x0 + (x1 - x0) * tick / 5
        x_pos = sx(x_value)
        pieces.append(f'<line x1="{x_pos:.2f}" y1="{top}" x2="{x_pos:.2f}" y2="{top + plot_height}" stroke="#e5e7eb"/>')
        pieces.append(f'<text x="{x_pos:.2f}" y="{top + plot_height + 22}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{x_value:.2f}</text>')
        y_value = y0 + (y1 - y0) * tick / 5
        y_pos = sy(y_value)
        pieces.append(f'<line x1="{left}" y1="{y_pos:.2f}" x2="{left + plot_width}" y2="{y_pos:.2f}" stroke="#e5e7eb"/>')
        pieces.append(f'<text x="{left - 10}" y="{y_pos + 4:.2f}" text-anchor="end" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{y_value:.2f}</text>')

    line_y0 = slope * x0 + intercept
    line_y1 = slope * x1 + intercept
    pieces.append(
        f'<line x1="{sx(x0):.2f}" y1="{sy(line_y0):.2f}" x2="{sx(x1):.2f}" y2="{sy(line_y1):.2f}" stroke="#111827" stroke-width="2" stroke-dasharray="7 5" clip-path="url(#plotclip)"/>'
    )
    pieces.insert(
        5,
        f'<defs><clipPath id="plotclip"><rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}"/></clipPath></defs>',
    )

    for row in rows:
        model = str(row["model"])
        model_class = str(row["model_class"])
        color = palette.get(model_class, "#059669")
        x_pos, y_pos = sx(float(row["x"])), sy(float(row["y"]))
        pieces.append(
            f'<circle cx="{x_pos:.2f}" cy="{y_pos:.2f}" r="4.2" fill="{color}" fill-opacity="0.72" stroke="#ffffff" stroke-width="0.8"><title>{html.escape(model)}; x={float(row["x"]):.4f}; y={float(row["y"]):.4f}</title></circle>'
        )
        if model in label_models:
            short = model.split("__")[-1]
            if len(short) > 28:
                short = short[:26] + "…"
            pieces.append(
                f'<text x="{x_pos + 6:.2f}" y="{y_pos - 6:.2f}" font-family="system-ui,sans-serif" font-size="10" fill="#991b1b">{html.escape(short)}</text>'
            )

    pieces.extend(
        [
            f'<text x="{left + plot_width / 2}" y="{height - 24}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111827">{html.escape(pair.x_label)} (higher = more capable)</text>',
            f'<text x="20" y="{top + plot_height / 2}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2})" font-family="system-ui,sans-serif" font-size="12" fill="#111827">{html.escape(pair.y_label)} (higher = safer)</text>',
            '<circle cx="610" cy="30" r="4" fill="#2563eb"/><text x="619" y="34" font-family="system-ui,sans-serif" font-size="11" fill="#374151">Instruct</text>',
            '<circle cx="682" cy="30" r="4" fill="#dc2626"/><text x="691" y="34" font-family="system-ui,sans-serif" font-size="11" fill="#374151">Reasoning</text>',
            '<circle cx="762" cy="30" r="4" fill="#059669"/><text x="771" y="34" font-family="system-ui,sans-serif" font-size="11" fill="#374151">Other</text>',
            '</svg>',
        ]
    )
    return "\n".join(pieces) + "\n"


def markdown_report(
    summary_rows: Sequence[dict[str, object]], residual_rows: Sequence[dict[str, object]]
) -> str:
    primary = [
        row
        for row in residual_rows
        if row["pair_id"] == "general_reasoning__selected_safety"
    ]
    primary.sort(key=lambda row: float(row["standardized_gap"]))
    report = [
        "# Preliminary BenchMIRT Capability–Safety Relationships",
        "",
        "**Status:** descriptive baseline; not a release decision",
        f"**Source:** BenchMIRT model-statistics dataset at commit `{SOURCE_COMMIT}`",
        f"**Panel:** {summary_rows[0]['n_models']} open-weight artifacts",
        "",
        "This baseline validates the data and analysis path using a common-protocol",
        "100-model panel. It covers broad reasoning, instruction following, behavioral",
        "safety, and an aggregated hazardous-knowledge proxy. It does **not** yet cover",
        "the required coding→cyber, biology, chemistry, or agentic domain pairs.",
        "",
        "## Relationship estimates",
        "",
        "Spearman intervals use a deterministic 2,000-replicate family-cluster bootstrap.",
        "Higher y-axis scores mean safer behavior in the source dataset.",
        "",
        "| Domain | Capability | Safety construct | n | Spearman ρ | 95% family-cluster interval | Pearson r |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        report.append(
            "| {domain} | {x_label} | {y_label} | {n_models} | {spearman_rho} | "
            "[{spearman_ci_low}, {spearman_ci_high}] | {pearson_r} |".format(**row)
        )

    report.extend(
        [
            "",
            "## Lowest broad-safety residuals",
            "",
            "These are the models furthest below the descriptive broad-safety trend after",
            "a simple linear fit to general reasoning. They are **candidates for deeper",
            "audit**, not findings that the models are unsafe or should be withheld.",
            "Lineage, protocol, and family effects have not yet been fully modeled.",
            "",
            "| Rank | Model | Family | Capability | Observed safety | Expected safety | Gap | Robust standardized gap |",
            "|---:|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for rank, row in enumerate(primary[:10], start=1):
        report.append(
            f"| {rank} | `{row['model']}` | {row['family']} | {float(row['x']):.3f} | "
            f"{float(row['y']):.3f} | {float(row['expected_y']):.3f} | "
            f"{float(row['safety_gap']):+.3f} | {float(row['standardized_gap']):+.2f} |"
        )

    report.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- The OLS line is an empirical expectation, not a normative safety envelope.",
            "- BenchMIRT's selected safety average excludes BBQ and WMDP but still combines",
            "  several behavioral constructs.",
            "- The source `family` field is publisher-oriented and does not fully represent",
            "  base-model lineage; lineage normalization is required before confirmatory work.",
            "- Aggregate model scores do not supply item-level uncertainty. The cluster",
            "  interval captures family sampling variation, not all measurement error.",
            "- WMDP is stored safety-oriented (`1 - correct`) and is hazardous-knowledge",
            "  evidence, not a behavioral refusal score.",
            "- No release classification should be derived from this preliminary report.",
            "",
            "## Next evidence step",
            "",
            "Audit exact artifact overlap for maintained coding benchmarks, CyberSecEval 4,",
            "and Cybench. That pair is the first domain-specific relationship intended for",
            "confirmatory analysis.",
            "",
        ]
    )
    return "\n".join(report)


def main() -> None:
    acquire_source()
    rows = load_rows()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, object]] = []
    residual_rows: list[dict[str, object]] = []

    for pair_number, pair in enumerate(PAIRS):
        usable = numeric_rows(rows, pair)
        xs = [float(row["x"]) for row in usable]
        ys = [float(row["y"]) for row in usable]
        slope, intercept = linear_fit(xs, ys)
        p_r = pearson(xs, ys)
        s_r = spearman(xs, ys)
        ci_low, ci_high = cluster_bootstrap_correlation(
            usable, BOOTSTRAP_SEED + pair_number
        )
        residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
        scale = residual_scale(residuals)

        pair_residuals: list[dict[str, object]] = []
        for row, residual in zip(usable, residuals):
            record = {
                "pair_id": pair.pair_id,
                "domain": pair.domain,
                "model": row["model"],
                "family": row["family"],
                "model_class": row["model_class"],
                "x": f"{float(row['x']):.8f}",
                "y": f"{float(row['y']):.8f}",
                "expected_y": f"{slope * float(row['x']) + intercept:.8f}",
                "safety_gap": f"{residual:.8f}",
                "standardized_gap": f"{residual / scale:.8f}",
            }
            residual_rows.append(record)
            pair_residuals.append(record)

        pair_residuals.sort(key=lambda row: float(row["standardized_gap"]))
        label_models = {
            str(row["model"])
            for row in pair_residuals[:5]
            if float(row["standardized_gap"]) <= -1.0
        }
        figure = svg_scatter(pair, usable, slope, intercept, label_models)
        (FIGURE_DIR / f"{pair.pair_id}.svg").write_text(figure, encoding="utf-8")

        summary_rows.append(
            {
                "pair_id": pair.pair_id,
                "domain": pair.domain,
                "x_column": pair.x_column,
                "y_column": pair.y_column,
                "x_label": pair.x_label,
                "y_label": pair.y_label,
                "construct": pair.construct,
                "n_models": len(usable),
                "n_families": len({str(row["family"]) for row in usable}),
                "pearson_r": fmt(p_r),
                "spearman_rho": fmt(s_r),
                "spearman_ci_low": fmt(ci_low),
                "spearman_ci_high": fmt(ci_high),
                "ols_slope": fmt(slope, 6),
                "ols_intercept": fmt(intercept, 6),
                "source_commit": SOURCE_COMMIT,
            }
        )

    write_csv(
        OUTPUT_DIR / "benchmirt_preliminary_relationships.csv",
        list(summary_rows[0]),
        summary_rows,
    )
    write_csv(
        OUTPUT_DIR / "benchmirt_preliminary_residuals.csv",
        list(residual_rows[0]),
        residual_rows,
    )
    (OUTPUT_DIR / "benchmirt_preliminary_report.md").write_text(
        markdown_report(summary_rows, residual_rows), encoding="utf-8"
    )
    print(
        f"Wrote {len(summary_rows)} relationships and {len(residual_rows)} residual rows "
        f"from {len(rows)} source artifacts."
    )


if __name__ == "__main__":
    main()
