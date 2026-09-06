#!/usr/bin/env python3
"""Estimate biomedical and chemistry proxy relationships in Safetywashing.

This common-source analysis keeps hazardous-knowledge suppression separate from
behavioral safeguards. MedQA is treated as a biomedical capability proxy and
GPQA as a broad science proxy; neither is a substitute for practical biology or
chemistry laboratory capability.
"""

from __future__ import annotations

import csv
import html
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from coding_cyber_exploratory import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    OUTPUT_DIR,
    FIGURE_DIR,
    SAFETYWASHING_FILE,
    acquire_sources,
    fmt,
    linear_fit,
    pearson,
    percentile,
    ranks,
    residual_scale,
    write_csv,
)


@dataclass(frozen=True)
class Pair:
    pair_id: str
    domain: str
    x_column: str
    y_column: str
    x_label: str
    y_label: str
    construct: str


HARM_BIO_COLUMNS = (
    "HarmBench-DirectRequest-chemical_biological",
    "HarmBench-HumanJailbreaks-chemical_biological",
    "HarmBench-TAP-T-chemical_biological",
    "HarmBench-GCG-T-chemical_biological",
)

PAIRS = (
    Pair(
        "medqa__bio_behavioral_composite",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        "bio_behavioral_composite",
        "MedQA four-option accuracy",
        "Biochemical HarmBench attack-failure composite",
        "biomedical knowledge versus harmful-compliance resistance",
    ),
    Pair(
        "medqa__wmdp_bio",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        "wmdp_bio",
        "MedQA four-option accuracy",
        "WMDP-Bio inverted accuracy",
        "biomedical knowledge versus lack of hazardous biological knowledge",
    ),
    Pair(
        "gpqa__wmdp_bio",
        "science_to_biological_misuse_proxy",
        "gpqa",
        "wmdp_bio",
        "GPQA exact match",
        "WMDP-Bio inverted accuracy",
        "broad science reasoning versus lack of hazardous biological knowledge",
    ),
    Pair(
        "gpqa__wmdp_chem",
        "science_to_chemical_misuse_proxy",
        "gpqa",
        "wmdp_chem",
        "GPQA exact match",
        "WMDP-Chem inverted accuracy",
        "broad science reasoning versus lack of hazardous chemical knowledge",
    ),
    Pair(
        "medqa__harmbench_bio_direct",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        HARM_BIO_COLUMNS[0],
        "MedQA four-option accuracy",
        "HarmBench biochemical direct-request attack failure",
        "direct harmful-compliance resistance",
    ),
    Pair(
        "medqa__harmbench_bio_human_jailbreak",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        HARM_BIO_COLUMNS[1],
        "MedQA four-option accuracy",
        "HarmBench biochemical human-jailbreak attack failure",
        "human-jailbreak resistance",
    ),
    Pair(
        "medqa__harmbench_bio_tap",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        HARM_BIO_COLUMNS[2],
        "MedQA four-option accuracy",
        "HarmBench biochemical TAP-T attack failure",
        "automated-jailbreak resistance",
    ),
    Pair(
        "medqa__harmbench_bio_gcg",
        "biomedical_to_biological_misuse",
        "medqa_4options",
        HARM_BIO_COLUMNS[3],
        "MedQA four-option accuracy",
        "HarmBench biochemical GCG-T attack failure",
        "adversarial-suffix resistance",
    ),
)


def family(model: str) -> str:
    lowered = model.lower()
    if "llama" in lowered:
        return "Llama"
    if "qwen" in lowered:
        return "Qwen"
    if "mistral" in lowered or "mixtral" in lowered:
        return "Mistral/Mixtral"
    if "gemma" in lowered:
        return "Gemma"
    if "falcon" in lowered:
        return "Falcon"
    if "deepseek" in lowered:
        return "DeepSeek"
    if lowered.startswith("yi-"):
        return "Yi"
    if "dbrx" in lowered:
        return "DBRX"
    raise ValueError(f"unassigned lineage: {model}")


def load_rows() -> list[dict[str, object]]:
    with SAFETYWASHING_FILE.open(newline="", encoding="utf-8") as handle:
        source = list(csv.DictReader(handle))
    rows: list[dict[str, object]] = []
    for row in source:
        augmented: dict[str, object] = dict(row)
        augmented["family"] = family(row["model"])
        augmented["bio_behavioral_composite"] = statistics.mean(
            float(row[column]) for column in HARM_BIO_COLUMNS
        )
        rows.append(augmented)
    return rows


def usable_rows(rows: Sequence[dict[str, object]], pair: Pair) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        try:
            x, y = float(row[pair.x_column]), float(row[pair.y_column])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            result.append({"model": row["model"], "family": row["family"], "x": x, "y": y})
    return result


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    return pearson(ranks(xs), ranks(ys))


def family_bootstrap(rows: Sequence[dict[str, object]], seed: int) -> tuple[float, float]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["family"]), []).append(row)
    lineages = sorted(groups)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled: list[dict[str, object]] = []
        for _ in lineages:
            sampled.extend(groups[rng.choice(lineages)])
        estimate = spearman(
            [float(row["x"]) for row in sampled],
            [float(row["y"]) for row in sampled],
        )
        if math.isfinite(estimate):
            estimates.append(estimate)
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def svg(pair: Pair, rows: Sequence[dict[str, object]], slope: float, intercept: float) -> str:
    width, height = 820, 540
    left, right, top, bottom = 86, 28, 64, 78
    plot_width, plot_height = width - left - right, height - top - bottom
    xs, ys = [float(row["x"]) for row in rows], [float(row["y"]) for row in rows]

    def extent(values: Sequence[float]) -> tuple[float, float]:
        low, high = min(values), max(values)
        padding = (high - low) * 0.08 if high > low else 0.05
        return low - padding, high + padding

    x0, x1 = extent(xs)
    y0, y1 = extent(ys)

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0) * plot_width

    def sy(value: float) -> float:
        return top + (y1 - value) / (y1 - y0) * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(pair.x_label)} versus {html.escape(pair.y_label)}</title>',
        f'<desc id="desc">Scatterplot of {len(rows)} open-weight chat checkpoints with a descriptive OLS trend.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="29" font-family="system-ui,sans-serif" font-size="18" font-weight="600" fill="#111827">{html.escape(pair.x_label)} vs. {html.escape(pair.y_label)}</text>',
        f'<text x="{left}" y="49" font-family="system-ui,sans-serif" font-size="12" fill="#4b5563">Safetywashing chat panel · n={len(rows)} · descriptive trend, not a release threshold</text>',
        f'<defs><clipPath id="{pair.pair_id}-clip"><rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}"/></clipPath></defs>',
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#ffffff" stroke="#9ca3af"/>',
    ]
    for tick in range(6):
        xv, yv = x0 + (x1 - x0) * tick / 5, y0 + (y1 - y0) * tick / 5
        xp, yp = sx(xv), sy(yv)
        parts.extend(
            [
                f'<line x1="{xp:.1f}" y1="{top}" x2="{xp:.1f}" y2="{top + plot_height}" stroke="#e5e7eb"/>',
                f'<text x="{xp:.1f}" y="{top + plot_height + 22}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{xv:.2f}</text>',
                f'<line x1="{left}" y1="{yp:.1f}" x2="{left + plot_width}" y2="{yp:.1f}" stroke="#e5e7eb"/>',
                f'<text x="{left - 10}" y="{yp + 4:.1f}" text-anchor="end" font-family="system-ui,sans-serif" font-size="11" fill="#374151">{yv:.2f}</text>',
            ]
        )
    parts.append(
        f'<line x1="{sx(x0):.1f}" y1="{sy(slope * x0 + intercept):.1f}" x2="{sx(x1):.1f}" y2="{sy(slope * x1 + intercept):.1f}" stroke="#111827" stroke-width="2" stroke-dasharray="7 5" clip-path="url(#{pair.pair_id}-clip)"/>'
    )
    for row in rows:
        x, y = float(row["x"]), float(row["y"])
        parts.append(
            f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4.5" fill="#2563eb" fill-opacity="0.75" stroke="#ffffff"><title>{html.escape(str(row["model"]))}: x={x:.3f}, y={y:.3f}</title></circle>'
        )
    parts.extend(
        [
            f'<text x="{left + plot_width / 2}" y="{height - 24}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111827">{html.escape(pair.x_label)} (higher = more capable)</text>',
            f'<text x="20" y="{top + plot_height / 2}" text-anchor="middle" transform="rotate(-90 20 {top + plot_height / 2})" font-family="system-ui,sans-serif" font-size="12" fill="#111827">{html.escape(pair.y_label)} (higher = safer)</text>',
            '</svg>',
        ]
    )
    return "\n".join(parts) + "\n"


def report(summary: Sequence[dict[str, object]], residuals: Sequence[dict[str, object]]) -> str:
    lines = [
        "# Biomedical and Chemistry Capability–Safety Proxies",
        "",
        "**Status:** common-source domain-proxy analysis; not a release decision",
        "**Panel:** 26 open-weight instruction/chat checkpoints across 8 lineages",
        "**Source:** Safetywashing chat-model matrix",
        "",
        "## Main findings",
        "",
        "- MedQA performance is strongly negatively associated with WMDP-Bio's inverted accuracy: models with more biomedical knowledge generally retain more hazardous biological knowledge.",
        "- MedQA performance has only weak negative relationships with biochemical HarmBench resistance. Knowledge and behavioral safeguards are therefore empirically distinct axes.",
        "- GPQA is also strongly negatively associated with WMDP-Bio and WMDP-Chem, but it is only a broad science proxy; a practical chemistry capability benchmark is still required.",
        "",
        "## Estimates",
        "",
        "All outcome scores are oriented so higher means safer. Intervals resample model lineages and do not capture item-level measurement error.",
        "",
        "| Domain | Capability | Safety outcome | n | Families | Spearman ρ | 95% family-cluster interval | Pearson r |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['domain']} | {row['x_label']} | {row['y_label']} | {row['n_models']} | {row['n_families']} | {row['spearman_rho']} | [{row['spearman_ci_low']}, {row['spearman_ci_high']}] | {row['pearson_r']} |"
        )
    lines.extend(
        [
            "",
            "## Lowest residuals on primary domain proxies",
            "",
            "These are comparative underperformance signals, not unsafe/release labels.",
            "",
            "| Pair | Model | Family | Capability | Safety | Expected | Gap | Robust standardized gap |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    primary = {"medqa__bio_behavioral_composite", "medqa__wmdp_bio", "gpqa__wmdp_chem"}
    for row in sorted(
        (item for item in residuals if item["pair_id"] in primary),
        key=lambda item: (str(item["pair_id"]), float(item["standardized_gap"])),
    ):
        if int(row["within_pair_rank"]) <= 5:
            lines.append(
                f"| {row['pair_id']} | `{row['model']}` | {row['family']} | {float(row['x']):.3f} | {float(row['y']):.3f} | {float(row['expected_y']):.3f} | {float(row['safety_gap']):+.3f} | {float(row['standardized_gap']):+.2f} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- MedQA is clinical multiple-choice knowledge, not wet-lab or bioweapon workflow capability.",
            "- GPQA is not a chemistry-specific capability measure.",
            "- WMDP is a hazardous-knowledge proxy and intentionally excludes sensitive operational detail.",
            "- HarmBench measures elicited text behavior, not real-world uplift or end-to-end task completion.",
            "- The pooled regression is descriptive. It cannot supply a normative public-release threshold.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    acquire_sources()
    rows = load_rows()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, object]] = []
    residuals: list[dict[str, object]] = []

    for index, pair in enumerate(PAIRS):
        usable = usable_rows(rows, pair)
        xs, ys = [float(row["x"]) for row in usable], [float(row["y"]) for row in usable]
        slope, intercept = linear_fit(xs, ys)
        ci_low, ci_high = family_bootstrap(usable, BOOTSTRAP_SEED + 100 + index)
        raw = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
        scale = residual_scale(raw)
        pair_rows: list[dict[str, object]] = []
        for row, gap in zip(usable, raw):
            item = {
                "pair_id": pair.pair_id,
                "model": row["model"],
                "family": row["family"],
                "x": f"{float(row['x']):.8f}",
                "y": f"{float(row['y']):.8f}",
                "expected_y": f"{slope * float(row['x']) + intercept:.8f}",
                "safety_gap": f"{gap:.8f}",
                "standardized_gap": f"{gap / scale:.8f}",
                "within_pair_rank": 0,
            }
            pair_rows.append(item)
            residuals.append(item)
        for rank, item in enumerate(sorted(pair_rows, key=lambda entry: float(entry["standardized_gap"])), start=1):
            item["within_pair_rank"] = rank

        summary.append(
            {
                "pair_id": pair.pair_id,
                "domain": pair.domain,
                "x_label": pair.x_label,
                "y_label": pair.y_label,
                "construct": pair.construct,
                "n_models": len(usable),
                "n_families": len({str(row["family"]) for row in usable}),
                "pearson_r": fmt(pearson(xs, ys)),
                "spearman_rho": fmt(spearman(xs, ys)),
                "spearman_ci_low": fmt(ci_low),
                "spearman_ci_high": fmt(ci_high),
                "ols_slope": fmt(slope, 6),
                "ols_intercept": fmt(intercept, 6),
            }
        )
        (FIGURE_DIR / f"{pair.pair_id}.svg").write_text(
            svg(pair, usable, slope, intercept), encoding="utf-8"
        )

    write_csv(OUTPUT_DIR / "domain_proxy_relationships.csv", summary, list(summary[0]))
    write_csv(OUTPUT_DIR / "domain_proxy_residuals.csv", residuals, list(residuals[0]))
    (OUTPUT_DIR / "domain_proxy_report.md").write_text(
        report(summary, residuals), encoding="utf-8"
    )
    print(f"Wrote {len(summary)} domain-proxy relationships from {len(rows)} checkpoints.")


if __name__ == "__main__":
    main()
