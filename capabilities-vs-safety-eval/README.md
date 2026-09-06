# Capability–Safety Research Workspace

This directory contains the reproducible evidence audit and analysis for the
domain-conditioned open-weight LLM release framework.

The governing plan is [`meta-analysis-plan.md`](meta-analysis-plan.md), with the
release framework specified in [`framework.md`](framework.md).
The initial BenchMIRT analysis is deliberately labeled **preliminary**: it
validates the pipeline and provides broad reasoning/instruction-following
relationships, but it does not establish domain release thresholds.

## Current stage

- Stage 1 source and coverage audit: in progress.
- Preliminary common-protocol panel: BenchMIRT, 100 open-weight LLMs.
- Exploratory coding→cyber panel: 8 exact checkpoints across 4 lineages.
- Biomedical/chemistry proxy panel: 26 checkpoints across 8 lineages.
- Domain-specific source panels: incomplete; practical biology, chemistry,
  agentic, and modern software-engineering joins require common-harness reruns.
- Normative release envelope: not yet calibrated.

## Reproduce the preliminary analysis

```bash
python3 capabilities-vs-safety-eval/analysis/benchmirt_baseline.py
python3 capabilities-vs-safety-eval/analysis/coding_cyber_exploratory.py
python3 capabilities-vs-safety-eval/analysis/domain_proxy_analysis.py
```

The scripts use only the Python standard library. They download commit-pinned
source files, verify SHA-256 digests, and regenerate the preliminary tables,
reports, and SVG plots.

Current reports:

- [`outputs/benchmirt_preliminary_report.md`](outputs/benchmirt_preliminary_report.md)
- [`outputs/coding_cyber_exploratory_report.md`](outputs/coding_cyber_exploratory_report.md)
- [`outputs/domain_proxy_report.md`](outputs/domain_proxy_report.md)

## Evidence rules

- Raw source values remain unchanged.
- Every score must retain its construct and direction.
- Exact artifact identity and evaluation protocol are required for
  confirmatory use.
- Missing data are not interpreted as pass or fail.
- Empirical residuals are comparative flags, not release decisions.
- Sensitive held-out prompts must never be committed here.
