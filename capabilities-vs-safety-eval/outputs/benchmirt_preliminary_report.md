# Preliminary BenchMIRT Capability–Safety Relationships

**Status:** descriptive baseline; not a release decision
**Source:** BenchMIRT model-statistics dataset at commit `8672cad1429df9e885f9651d3ae01621748662f5`
**Panel:** 100 open-weight artifacts

This baseline validates the data and analysis path using a common-protocol
100-model panel. It covers broad reasoning, instruction following, behavioral
safety, and an aggregated hazardous-knowledge proxy. It does **not** yet cover
the required coding→cyber, biology, chemistry, or agentic domain pairs.

## Relationship estimates

Spearman intervals use a deterministic 2,000-replicate family-cluster bootstrap.
Higher y-axis scores mean safer behavior in the source dataset.

| Domain | Capability | Safety construct | n | Spearman ρ | 95% family-cluster interval | Pearson r |
|---|---|---|---:|---:|---:|---:|
| general_reasoning | General reasoning average | Selected behavioral safety average | 100 | 0.248 | [0.006, 0.480] | 0.291 |
| general_reasoning | General reasoning average | HarmBench safety score | 100 | 0.371 | [0.121, 0.607] | 0.387 |
| general_reasoning | General reasoning average | StrongREJECT safety score | 100 | -0.065 | [-0.346, 0.174] | -0.070 |
| general_reasoning | General reasoning average | WildJailbreak safety score | 100 | 0.240 | [0.045, 0.423] | 0.223 |
| general_reasoning | General reasoning average | XSTest balanced safety score | 100 | 0.482 | [0.234, 0.697] | 0.479 |
| scientific_hazardous_knowledge_proxy | General reasoning average | WMDP safety-oriented score | 100 | -0.876 | [-0.945, -0.784] | -0.861 |
| scientific_hazardous_knowledge_proxy | GPQA accuracy | WMDP safety-oriented score | 100 | -0.704 | [-0.811, -0.585] | -0.692 |
| instruction_following | IFEval accuracy | HarmBench safety score | 100 | 0.413 | [0.155, 0.607] | 0.419 |
| instruction_following | IFEval accuracy | StrongREJECT safety score | 100 | 0.017 | [-0.269, 0.239] | 0.017 |
| instruction_following | IFEval accuracy | WildJailbreak safety score | 100 | 0.254 | [-0.019, 0.462] | 0.320 |
| instruction_following | IFEval accuracy | TrustLLM JailbreakTrigger safety score | 100 | 0.334 | [0.093, 0.500] | 0.340 |
| instruction_following | IFEval accuracy | XSTest balanced safety score | 100 | 0.622 | [0.448, 0.759] | 0.577 |

## Lowest broad-safety residuals

These are the models furthest below the descriptive broad-safety trend after
a simple linear fit to general reasoning. They are **candidates for deeper
audit**, not findings that the models are unsafe or should be withheld.
Lineage, protocol, and family effects have not yet been fully modeled.

| Rank | Model | Family | Capability | Observed safety | Expected safety | Gap | Robust standardized gap |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `cognitivecomputations__dolphin-2.9.1-yi-1.5-34b` | dphn | 0.526 | 0.229 | 0.596 | -0.367 | -1.89 |
| 2 | `cognitivecomputations__dolphin-2.9.3-Yi-1.5-34B-32k` | dphn | 0.531 | 0.306 | 0.598 | -0.291 | -1.50 |
| 3 | `cognitivecomputations__dolphin-2.9.2-Phi-3-Medium-abliterated` | dphn | 0.537 | 0.310 | 0.600 | -0.290 | -1.49 |
| 4 | `teknium__OpenHermes-2.5-Mistral-7B` | teknium | 0.391 | 0.288 | 0.543 | -0.255 | -1.31 |
| 5 | `cognitivecomputations__dolphin-2.9.1-llama-3-70b` | dphn | 0.475 | 0.344 | 0.576 | -0.231 | -1.19 |
| 6 | `NousResearch__DeepHermes-3-Mistral-24B-Preview` | NousResearch | 0.548 | 0.379 | 0.604 | -0.225 | -1.16 |
| 7 | `mlabonne__NeuralDaredevil-8B-abliterated` | mlabonne | 0.457 | 0.354 | 0.569 | -0.215 | -1.11 |
| 8 | `cognitivecomputations__dolphin-2.9.2-qwen2-72b` | dphn | 0.608 | 0.413 | 0.628 | -0.215 | -1.11 |
| 9 | `refuelai__Llama-3-Refueled` | refuelai | 0.425 | 0.349 | 0.556 | -0.208 | -1.07 |
| 10 | `NousResearch__Nous-Hermes-2-SOLAR-10.7B` | NousResearch | 0.434 | 0.356 | 0.560 | -0.204 | -1.05 |

## Interpretation limits

- The OLS line is an empirical expectation, not a normative safety envelope.
- BenchMIRT's selected safety average excludes BBQ and WMDP but still combines
  several behavioral constructs.
- The source `family` field is publisher-oriented and does not fully represent
  base-model lineage; lineage normalization is required before confirmatory work.
- Aggregate model scores do not supply item-level uncertainty. The cluster
  interval captures family sampling variation, not all measurement error.
- WMDP is stored safety-oriented (`1 - correct`) and is hazardous-knowledge
  evidence, not a behavioral refusal score.
- No release classification should be derived from this preliminary report.

## Next evidence step

Audit exact artifact overlap for maintained coding benchmarks, CyberSecEval 4,
and Cybench. That pair is the first domain-specific relationship intended for
confirmatory analysis.
