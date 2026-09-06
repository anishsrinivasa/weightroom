# Biomedical and Chemistry Capability–Safety Proxies

**Status:** common-source domain-proxy analysis; not a release decision
**Panel:** 26 open-weight instruction/chat checkpoints across 8 lineages
**Source:** Safetywashing chat-model matrix

## Main findings

- MedQA performance is strongly negatively associated with WMDP-Bio's inverted accuracy: models with more biomedical knowledge generally retain more hazardous biological knowledge.
- MedQA performance has only weak negative relationships with biochemical HarmBench resistance. Knowledge and behavioral safeguards are therefore empirically distinct axes.
- GPQA is also strongly negatively associated with WMDP-Bio and WMDP-Chem, but it is only a broad science proxy; a practical chemistry capability benchmark is still required.

## Estimates

All outcome scores are oriented so higher means safer. Intervals resample model lineages and do not capture item-level measurement error.

| Domain | Capability | Safety outcome | n | Families | Spearman ρ | 95% family-cluster interval | Pearson r |
|---|---|---|---:|---:|---:|---:|---:|
| biomedical_to_biological_misuse | MedQA four-option accuracy | Biochemical HarmBench attack-failure composite | 26 | 8 | -0.253 | [-0.677, 0.121] | -0.162 |
| biomedical_to_biological_misuse | MedQA four-option accuracy | WMDP-Bio inverted accuracy | 26 | 8 | -0.985 | [-0.996, -0.941] | -0.942 |
| science_to_biological_misuse_proxy | GPQA exact match | WMDP-Bio inverted accuracy | 26 | 8 | -0.922 | [-0.966, -0.887] | -0.812 |
| science_to_chemical_misuse_proxy | GPQA exact match | WMDP-Chem inverted accuracy | 26 | 8 | -0.927 | [-0.981, -0.883] | -0.890 |
| biomedical_to_biological_misuse | MedQA four-option accuracy | HarmBench biochemical direct-request attack failure | 26 | 8 | -0.055 | [-0.540, 0.322] | -0.024 |
| biomedical_to_biological_misuse | MedQA four-option accuracy | HarmBench biochemical human-jailbreak attack failure | 26 | 8 | -0.283 | [-0.627, -0.023] | -0.262 |
| biomedical_to_biological_misuse | MedQA four-option accuracy | HarmBench biochemical TAP-T attack failure | 26 | 8 | -0.252 | [-0.653, 0.149] | -0.127 |
| biomedical_to_biological_misuse | MedQA four-option accuracy | HarmBench biochemical GCG-T attack failure | 26 | 8 | -0.201 | [-0.714, 0.167] | -0.159 |

## Lowest residuals on primary domain proxies

These are comparative underperformance signals, not unsafe/release labels.

| Pair | Model | Family | Capability | Safety | Expected | Gap | Robust standardized gap |
|---|---|---|---:|---:|---:|---:|---:|
| gpqa__wmdp_chem | `Qwen1.5-72B-Chat` | Qwen | 0.330 | 0.549 | 0.633 | -0.084 | -2.01 |
| gpqa__wmdp_chem | `Qwen1.5-4B-Chat` | Qwen | 0.268 | 0.745 | 0.820 | -0.075 | -1.80 |
| gpqa__wmdp_chem | `Mixtral-8x7B-Instruct-v0.1` | Mistral/Mixtral | 0.324 | 0.588 | 0.651 | -0.062 | -1.49 |
| gpqa__wmdp_chem | `gemma-1.1-7b-it` | Gemma | 0.298 | 0.683 | 0.730 | -0.047 | -1.12 |
| gpqa__wmdp_chem | `deepseek-llm-67b-chat` | DeepSeek | 0.322 | 0.611 | 0.657 | -0.046 | -1.10 |
| medqa__bio_behavioral_composite | `Qwen1.5-1.8B-Chat` | Qwen | 0.361 | 11.426 | 52.906 | -41.480 | -1.70 |
| medqa__bio_behavioral_composite | `Mistral-7B-Instruct-v0.2` | Mistral/Mixtral | 0.507 | 19.803 | 48.436 | -28.633 | -1.18 |
| medqa__bio_behavioral_composite | `dbrx-instruct` | DBRX | 0.684 | 14.474 | 43.004 | -28.530 | -1.17 |
| medqa__bio_behavioral_composite | `Mixtral-8x22B-Instruct-v0.1` | Mistral/Mixtral | 0.689 | 19.803 | 42.860 | -23.058 | -0.95 |
| medqa__bio_behavioral_composite | `Qwen1.5-7B-Chat` | Qwen | 0.455 | 26.974 | 50.022 | -23.047 | -0.95 |
| medqa__wmdp_bio | `gemma-1.1-7b-it` | Gemma | 0.457 | 0.450 | 0.533 | -0.083 | -1.70 |
| medqa__wmdp_bio | `Llama-2-13b-chat-hf` | Llama | 0.440 | 0.487 | 0.554 | -0.067 | -1.38 |
| medqa__wmdp_bio | `Llama-2-70b-chat-hf` | Llama | 0.498 | 0.419 | 0.483 | -0.064 | -1.32 |
| medqa__wmdp_bio | `gemma-1.1-2b-it` | Gemma | 0.290 | 0.684 | 0.738 | -0.054 | -1.10 |
| medqa__wmdp_bio | `Qwen1.5-4B-Chat` | Qwen | 0.416 | 0.532 | 0.583 | -0.051 | -1.05 |

## Interpretation limits

- MedQA is clinical multiple-choice knowledge, not wet-lab or bioweapon workflow capability.
- GPQA is not a chemistry-specific capability measure.
- WMDP is a hazardous-knowledge proxy and intentionally excludes sensitive operational detail.
- HarmBench measures elicited text behavior, not real-world uplift or end-to-end task completion.
- The pooled regression is descriptive. It cannot supply a normative public-release threshold.
