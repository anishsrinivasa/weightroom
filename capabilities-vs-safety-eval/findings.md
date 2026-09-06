# Capability-Conditioned Safety: Current Findings and Release Recommendation

**Date:** 2026-09-05
**Scope:** public evidence for text-only open-weight LLMs
**Status:** completed public-data baseline; operational release calibration still requires new common-harness evaluations

## Bottom line

The project's core idea is supported, but the simple decision rule “release if a
model is above the safety-versus-capability line” is not.

The literature and reconstructed public panels show that:

1. capability–safety relationships are strongly domain- and construct-specific;
2. hazardous knowledge can rise predictably with domain capability while
   behavioral safeguards remain largely independent;
3. model lineage and evaluation scaffolds can dominate the apparent
   relationship;
4. a regression residual is useful for finding unusually weak safeguards, but
   cannot establish acceptable absolute risk;
5. public weights require testing how quickly safeguards can be removed or
   bypassed.

The recommended release rule is therefore a **non-compensatory minimum safety
envelope**: every activated high-risk domain must pass an absolute hazardous-
capability ceiling, absolute safeguard and benign-utility floors, a relative
underperformance check, an evidence sufficiency gate, and a modification audit.

## What appears novel

The individual ingredients are not novel. [Safetywashing](https://arxiv.org/abs/2407.21792)
correlates general capability with many safety benchmarks; frontier safety
frameworks use capability thresholds and domain risk tiers; WMDP and related
work link dangerous knowledge to mitigation; and responsible-scaling policies
use release/deployment gates.

The likely novel contribution is the integrated formulation:

- pair **domain-specific productive capability** with **corresponding hazardous
  capability, safeguards, and benign controls**;
- estimate a capability-conditioned expectation for exact open-weight artifacts;
- use negative residuals to identify underperforming safeguards;
- keep empirical expectation separate from a normative risk-calibrated floor;
- require post-modification evidence before public-weight release;
- combine domains conjunctively rather than averaging them into one safety score.

This should be presented as a novel synthesis and operationalization, not as the
first use of capability–safety correlation.

## Reconstructed evidence panels

| Panel | Artifacts | Families | Use | Status |
|---|---:|---:|---|---|
| BenchMIRT | 100 | many; source family is publisher-oriented | broad reasoning and instruction-following relationships | Descriptive |
| Safetywashing chat models | 26 | 8 normalized lineages | biomedical/chemistry proxy relationships | Descriptive proxy |
| EvalPlus + Safetywashing exact join | 8 | 4 | coding→cyber relationship | Exploratory only |
| SWE-bench / LiveCodeBench / Cybench | modern but non-overlapping profiles | mixed | candidate common-harness sources | Not joinable as published |

## Main estimates

### Broad capability and safety

On 100 BenchMIRT open-weight artifacts:

| Pair | Spearman ρ | Family-cluster 95% interval |
|---|---:|---:|
| General reasoning → selected behavioral safety | 0.248 | [0.006, 0.480] |
| General reasoning → HarmBench | 0.371 | [0.121, 0.607] |
| General reasoning → StrongREJECT | -0.065 | [-0.346, 0.174] |
| General reasoning → XSTest | 0.482 | [0.234, 0.697] |
| General reasoning → WMDP safety-oriented score | -0.876 | [-0.945, -0.784] |
| IFEval → XSTest | 0.622 | [0.448, 0.759] |

The wide variation means there is no defensible generic “capability–safety
correlation.” The relationship must be estimated per construct.

### Coding→cyber

Across eight exact checkpoints shared by EvalPlus and Safetywashing:

| Pair | Spearman ρ | Family-cluster 95% interval | Conclusion |
|---|---:|---:|---|
| EvalPlus mean → cyber behavioral-safety composite | -0.190 | [-0.750, 0.200] | Too uncertain for inference |
| EvalPlus mean → CyberSecEval 2 secure autocomplete | -0.738 | [-1.000, -0.481] | Strong negative signal in this small panel; lineage-confounded |
| EvalPlus mean → WMDP-Cyber inverted accuracy | -0.952 | [-1.000, -0.893] | Hazardous cyber knowledge rises with code capability in this panel |

The composite relationship is weak because safeguard behavior differs more by
lineage than by code capability. This is precisely why a capability-conditioned
residual is useful for audit but unsafe as a release threshold.

### Biomedical and chemistry proxies

Across 26 exact chat/instruction checkpoints:

| Pair | Spearman ρ | Family-cluster 95% interval | Conclusion |
|---|---:|---:|---|
| MedQA → WMDP-Bio inverted accuracy | -0.985 | [-0.996, -0.941] | More biomedical knowledge closely tracks more hazardous biological knowledge |
| MedQA → biochemical HarmBench composite | -0.253 | [-0.677, 0.121] | Refusal/jailbreak safety is not explained well by biomedical capability |
| GPQA → WMDP-Chem inverted accuracy | -0.927 | [-0.981, -0.883] | More science capability tracks more hazardous chemistry knowledge |

This supports two separate gates: a hazardous-capability ceiling and a
behavioral-safeguard floor.

## Models flagged for deeper audit

These are comparative residual flags only. They are not findings that a model
is unsafe or should be withheld.

| Analysis | Lowest-residual candidates |
|---|---|
| Broad reasoning → behavioral safety | `cognitivecomputations__dolphin-2.9.1-yi-1.5-34b`, `cognitivecomputations__dolphin-2.9.3-Yi-1.5-34B-32k`, `cognitivecomputations__dolphin-2.9.2-Phi-3-Medium-abliterated`, plus several other Dolphin/abliterated derivatives |
| Coding → cyber behavioral safeguards | `Mistral-7B-Instruct-v0.2`, `Mixtral-8x22B-Instruct-v0.1`, `Mixtral-8x7B-Instruct-v0.1`, `Qwen1.5-72B-Chat` |
| MedQA → biochemical behavioral safeguards | `Qwen1.5-1.8B-Chat`, `Mistral-7B-Instruct-v0.2`, `dbrx-instruct`, `Mixtral-8x22B-Instruct-v0.1`, `Qwen1.5-7B-Chat` |
| MedQA → WMDP-Bio suppression | `gemma-1.1-7b-it`, `Llama-2-13b-chat-hf`, `Llama-2-70b-chat-hf`, `gemma-1.1-2b-it`, `Qwen1.5-4B-Chat` |
| GPQA → WMDP-Chem suppression | `Qwen1.5-72B-Chat`, `Qwen1.5-4B-Chat`, `Mixtral-8x7B-Instruct-v0.1`, `gemma-1.1-7b-it`, `deepseek-llm-67b-chat` |

Most flags cluster by lineage or fine-tuning style. Confirmatory evaluation must
hold out entire lineages and include aligned, base, safety-tuned, and
refusal-removed controls.

## Recommended release logic

For every activated domain `d`, require:

```text
evidence is complete and protocol-matched
AND upper confidence bound on hazardous capability is below κ_d
AND lower confidence bound on every critical safeguard exceeds its absolute floor
AND lower confidence bound on matched benign utility exceeds its floor
AND safety residual is not worse than the allowed anti-regression margin
AND the same conditions hold after the declared modification budget
```

The public-weight decision passes only if **all** activated domains pass. No
strong score in one domain offsets failure in another.

Correlation contributes through an evidence-distinctness term such as
`1 - partial_R²(safety ~ capability + lineage + model class)`. It should not
directly set the permissible risk. The hazardous ceiling and absolute safety
floors must be calibrated against expert uplift or end-to-end harm anchors and a
predeclared risk tolerance.

See [`minimum-safety-envelope.md`](minimum-safety-envelope.md) for the full rule.

## Evidence still required before operational use

The first calibration study should evaluate at least 24 exact artifacts across
at least six independent lineages, including base, standard instruct,
safety-tuned, deliberately weakened, and contemporary high-capability models.
For the first critical vertical, run:

- LiveCodeBench/BigCodeBench and a fixed-profile SWE-bench task set;
- CyberSecEval secure-code, harmful-compliance, and prompt-injection tracks;
- Cybench under a bounded strong-elicitation profile;
- matched benign cyber assistance and false-refusal controls;
- system-prompt removal, alternate template, quantization, jailbreak, and
  bounded refusal-removal/fine-tuning tests;
- lineage-held-out validation and item-level uncertainty.

For biology and chemistry, replace the current MedQA/GPQA proxies with text-only
LAB-Bench/LABBench2 and ChemBench, plus expert-designed uplift and workflow tasks.

## Reproducibility

Run:

```bash
python3 capabilities-vs-safety-eval/analysis/benchmirt_baseline.py
python3 capabilities-vs-safety-eval/analysis/coding_cyber_exploratory.py
python3 capabilities-vs-safety-eval/analysis/domain_proxy_analysis.py
```

All scripts use commit-pinned source files, verify SHA-256 digests, and generate
deterministic CSV, Markdown, and SVG outputs.
