# Exploratory Coding→Cyber Capability–Safety Panel

**Status:** exploratory coverage diagnostic; not a release decision
**Capability source:** EvalPlus at `87418456d3a8c2bc6265f24ff91d2dd0e7993f4b`
**Safety source:** Safetywashing at `bee302c11f174afb064d721123b16b810d34f4fd`
**Exact overlap:** 8 instruction/chat checkpoints across 4 model families

## Main finding

The descriptive association between code correctness and the cyber behavioral-safety composite is weakly negative (Spearman ρ=-0.190; Pearson r=-0.182). The family-cluster bootstrap interval is [-0.750, 0.200], which is too wide for confirmatory inference.

The plot is more informative as a lineage diagnostic: all three Mistral/Mixtral checkpoints fall below the pooled trend, while both Meta Llama 3 and both Gemma 1.1 checkpoints fall above it. With only four families, lineage/alignment choices are inseparable from model capability. The pooled slope must not be used as a release boundary.

## Relationship estimates

All behavioral scores are oriented so higher means safer. WMDP is already stored as inverted accuracy (higher means less hazardous knowledge). The vulnerability-detection metric is dual-use capability evidence, not a safeguard score. Neither nonbehavioral metric is included in the behavioral-safety composite.

| Outcome | Construct | Direction | n | Spearman ρ | 95% family-cluster interval | Pearson r |
|---|---|---|---:|---:|---:|---:|
| Cyber behavioral-safety composite | descriptive mean of eight behavioral safeguard metrics | higher_is_safer | 8 | -0.190 | [-0.750, 0.200] | -0.182 |
| HarmBench cyber direct-request attack failure | harmful-compliance resistance | higher_is_safer | 8 | -0.214 | [-0.750, 0.309] | -0.176 |
| HarmBench cyber human-jailbreak attack failure | human-jailbreak resistance | higher_is_safer | 8 | 0.144 | [-1.000, 0.558] | -0.102 |
| HarmBench cyber TAP-T attack failure | automated-jailbreak resistance | higher_is_safer | 8 | 0.119 | [-0.750, 0.644] | 0.019 |
| HarmBench cyber GCG-T attack failure | adversarial-suffix resistance | higher_is_safer | 8 | -0.240 | [-1.000, 0.504] | -0.368 |
| CyberSecEval 2 instruct safe-suggestion rate | secure code instruction behavior | higher_is_safer | 8 | -0.548 | [-1.000, 0.400] | -0.719 |
| CyberSecEval 2 MITRE safe-response rate | offensive-cyber harmful-compliance resistance | higher_is_safer | 8 | 0.429 | [-1.000, 1.000] | 0.407 |
| CyberSecEval 2 prompt-injection attack failure | prompt-injection resistance | higher_is_safer | 8 | -0.072 | [-0.866, 1.000] | -0.427 |
| CyberSecEval 2 autocomplete safe-suggestion rate | secure code completion behavior | higher_is_safer | 8 | -0.738 | [-1.000, -0.481] | -0.829 |
| CyberSecEval 2 vulnerability detection rate | dual-use vulnerability-detection capability; not a safeguard score | higher_is_better_source_metric | 8 | -0.476 | [-1.000, 0.226] | -0.319 |
| WMDP-Cyber inverted accuracy | lack of hazardous cyber knowledge; Safetywashing stores an inverted score | higher_is_safer | 8 | -0.952 | [-1.000, -0.893] | -0.918 |

## Composite residual audit

Negative gaps indicate lower behavioral safeguards than the pooled eight-model trend predicts. These are triage signals only.

| Model | Family | Code capability | Observed safety | Expected safety | Gap | Robust standardized gap |
|---|---|---:|---:|---:|---:|---:|
| `Mistral-7B-Instruct-v0.2` | Mistral/Mixtral | 36.5 | 43.0 | 58.7 | -15.7 | -0.80 |
| `Mixtral-8x22B-Instruct-v0.1` | Mistral/Mixtral | 68.2 | 40.2 | 54.0 | -13.8 | -0.70 |
| `Mixtral-8x7B-Instruct-v0.1` | Mistral/Mixtral | 44.6 | 44.0 | 57.5 | -13.4 | -0.69 |
| `Qwen1.5-72B-Chat` | Qwen 1.5 | 60.4 | 48.3 | 55.1 | -6.9 | -0.35 |
| `gemma-1.1-2b-it` | Gemma 1.1 | 20.5 | 68.2 | 61.1 | +7.2 | +0.36 |
| `gemma-1.1-7b-it` | Gemma 1.1 | 40.2 | 70.2 | 58.1 | +12.1 | +0.62 |
| `Meta-Llama-3-70B-Instruct` | Meta Llama 3 | 70.5 | 66.6 | 53.6 | +13.0 | +0.66 |
| `Meta-Llama-3-8B-Instruct` | Meta Llama 3 | 55.8 | 73.3 | 55.8 | +17.5 | +0.89 |

## Why this cannot yet answer the release question

- The panel has fewer than the planned 12 artifacts and only four lineages.
- EvalPlus uses short Python synthesis tasks; it is not equivalent to repository-level autonomous software engineering.
- The safety composite averages heterogeneous constructs for visualization only. A release rule must preserve per-test floors.
- Public benchmark aggregates do not expose item-level covariance or uncertainty here.
- Open weights permit removal or replacement of the tested chat template and alignment behavior.
- SWE-bench scores depend on the agent scaffold, tools, attempts, and budget. They cannot be joined to bare-model safety scores without rerunning a fixed profile.

## Required next measurement

Run a registered panel of at least 12 exact open-weight checkpoints from at least four independent lineages under one inference stack. Measure LiveCodeBench/BigCodeBench, a fixed SWE-bench agent profile, CyberSecEval secure-code and harmful-compliance tracks, Cybench under bounded strong elicitation, benign cyber utility, and post-modification safety. Only then fit a candidate minimum envelope.
