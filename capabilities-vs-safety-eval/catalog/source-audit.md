# Stage 1 Source and Coverage Audit

**Audit date:** 2026-09-05
**Scope:** text-only/open-weight LLMs; public capability and corresponding safety evidence
**Decision status:** insufficient evidence for a public-weight release classifier

## Executive finding

The public literature supports estimating capability–safety relationships, but
it does not yet provide a complete, current, protocol-matched dataset from which
to learn a release boundary. Three usable panels were found:

1. **Broad common-protocol panel:** BenchMIRT, 100 open-weight artifacts. This
   is adequate for descriptive general reasoning/instruction-following analyses,
   but not for most domain-specific release claims.
2. **Biomedical/chemistry proxy panel:** 26 instruction/chat checkpoints in
   Safetywashing. MedQA or GPQA can be related to WMDP and HarmBench slices, but
   the capability tests are knowledge proxies rather than practical laboratory
   or workflow evaluations.
3. **Coding→cyber exploratory panel:** eight exact checkpoints shared by
   EvalPlus and Safetywashing. This is useful for diagnosing construct and
   lineage effects but is below the preregistered sample-size threshold.

Modern SWE-bench, LiveCodeBench, Cybench, agentic, multilingual, privacy, and
other specialist leaderboards generally do not evaluate the same exact open
checkpoints under compatible inference profiles. The main implementation need
is therefore a **common-harness rerun panel**, not more naïve leaderboard joins.

## Machine-readable sources verified

| Source | Snapshot | Rows / profiles inspected | Exact usable overlap | Audit conclusion |
|---|---|---:|---:|---|
| [Safetywashing](https://github.com/centerforaisafety/safetywashing) | `bee302c11f174afb064d721123b16b810d34f4fd` | 27 base; 26 chat; 181–183 fields | 26 internally; 8 with EvalPlus | Strongest public domain-proxy matrix, but 2023–2024 model population |
| [BenchMIRT model statistics](https://huggingface.co/datasets/allenai/BenchMIRT-model-statistics) | `8672cad1429df9e885f9651d3ae01621748662f5` | 100 open-weight artifacts; 28 fields | 100 internally | Best broad common-protocol panel; few domain-specific capability tests |
| [EvalPlus](https://github.com/evalplus/evalplus.github.io) | `87418456d3a8c2bc6265f24ff91d2dd0e7993f4b` | 125 model entries | 8 with Safetywashing chat | Supports an exploratory code→cyber join only |
| [SWE-bench leaderboard](https://github.com/SWE-bench/swe-bench.github.io) | `193160a463a435d05cf44a1fa9dc5eac832113c3` | 180 Verified submissions; 35 marked open-model | 0 admissible modern safety joins | Score belongs to an agent/model/profile; fixed-profile rerun required |
| [LiveCodeBench site data](https://github.com/LiveCodeBench/livecodebench.github.io) | `f186ab5041d6b768733b8ae8145151c867942134` | 36 profiles; 31,680 item rows in `v5.json` | 0 admissible maintained cyber-safety joins | Valuable capability source after common-panel reruns |
| [Cybench leaderboard](https://github.com/cybench/cybench.github.io) | `3b62a0d0e3105cef73a1d835d603888328f5c2bb` | 24 current profiles; 6 legacy item-level profiles | 0 admissible safeguard joins | Measures cyber capability, not whether safeguards resist misuse |

Digests, licenses, and file-level metadata are recorded in
[`sources.yaml`](sources.yaml).

## Domain coverage matrix

The readiness label applies to estimating a relationship for public-weight
release, not merely to whether a benchmark exists.

| Domain | Capability evidence | Corresponding safety evidence | Public exact overlap | Readiness | Required next action |
|---|---|---|---:|---|---|
| General reasoning | BenchMIRT reasoning composite, GPQA, MMLU-Pro | HarmBench, StrongREJECT, WildJailbreak, XSTest | 100 | Descriptive | Add consequential-risk anchors; do not treat broad residuals as release labels |
| Instruction following | IFEval | HarmBench, StrongREJECT, WildJailbreak, TrustLLM, XSTest | 100 | Descriptive | Add unsafe tool arguments and hierarchy-conflict tasks under the same profile |
| Software engineering→cyber | EvalPlus; SWE-bench; LiveCodeBench | HarmBench cyber; CyberSecEval 2 | 8 for EvalPlus; 0 admissible for SWE/LCB | Exploratory | Rerun 12+ exact artifacts on fixed code and cyber profiles |
| Cybersecurity capability | [Cybench](https://cybench.github.io/) | CyberSecEval harmful compliance, secure coding, prompt injection; benign defensive controls | 0 current admissible | Not estimable | Evaluate the same models with the same scaffold and bounded elicitation budget |
| Biology/biotechnology | MedQA and GPQA proxies; [LAB-Bench](https://github.com/Future-House/lab-bench) | WMDP-Bio; HarmBench biochemical; expert uplift | 26 for proxies; none practical | Proxy only | Run text-only LAB-Bench/LABBench2 and expert-designed uplift tasks on common artifacts |
| Chemistry | GPQA proxy; [ChemBench](https://github.com/lamalab-org/chembench) | WMDP-Chem; [ChemSafetyBench](https://github.com/HaochenZhao/SafeAgent4Chem) | 26 for GPQA/WMDP; no practical pair | Proxy only | Run ChemBench text subset and ChemSafetyBench on common artifacts |
| Medicine/health | [HealthBench](https://openai.com/index/healthbench/), MedQA | contraindication, urgent escalation, dosage, uncertainty, false reassurance | MedQA proxy only | Not release-calibrated | Predeclare high-acuity strata; add physician adjudication and benign utility |
| Text agents/tool use | [BFCL](https://github.com/EnlightenedAI/BFCL), tau-bench | [AgentHarm](https://arxiv.org/abs/2410.09024), [AgentDojo](https://agentdojo.spylab.ai/) | insufficient exact open-weight panel | Not estimable | Common tool schema, permissions, attack set, and utility controls |
| Long-horizon autonomy/AI R&D | [RE-Bench](https://github.com/METR/RE-Bench), [MLE-bench](https://github.com/openai/mle-bench) | sabotage, oversight evasion, replication/exfiltration | no public paired panel | Not estimable | Treat as a separate agent-profile evaluation; expert/held-out tasks required |
| Persuasion/manipulation | controlled belief-shift studies | coercion, fraud, targeting, impersonation, vulnerable-population harms | heterogeneous human studies | Not estimable | A registered human-subject protocol with matched benign persuasion is required |
| Finance | [FinanceBench](https://arxiv.org/abs/2311.11944), FinQA | fraud, suitability, manipulation, uncertainty, high-stakes advice | no suitable paired panel | Not estimable | Build claim-conditioned expert safety rubrics and evaluate exact artifacts |
| Law | [LegalBench](https://github.com/HazyResearch/legalbench) | fabricated citation, deadlines, confidentiality, unauthorized practice | no suitable paired panel | Not estimable | Jurisdiction-specific expert evaluation and calibrated abstention controls |
| Privacy/data handling | retrieval/long-context tasks | [PrivacyLens](https://github.com/SALT-NLP/PrivacyLens), canaries, exfiltration | several older models, but no matched capability construct | Low | Pair extraction utility with leakage under the same context and agent permissions |
| Multilingual/cross-cultural | [MMLU-ProX](https://mmluprox.github.io/) | XSafety, MultiJail, [XL-SafetyBench](https://github.com/AIM-Intelligence/XL-SafetyBench) | not yet sufficient under a shared protocol | Low | Evaluate per language; do not pool translations into one English-equivalent score |
| Long-context/RAG | RULER, LongBench, grounded QA | indirect prompt injection, poisoned retrieval, confidential-context exfiltration | no comparable bare-model panel | Low | Bind results to a fixed RAG scaffold and access-control policy |

## Empirical estimates now available

### Broad and instruction-following panel

BenchMIRT provides 100-model descriptive relationships. The most relevant
result is that relationships differ sharply by safety construct: reasoning has
a small positive relationship with the broad selected-safety average, a moderate
positive relationship with XSTest, and a strong negative relationship with the
WMDP safety-oriented score. See
[`../outputs/benchmirt_preliminary_report.md`](../outputs/benchmirt_preliminary_report.md).

### Coding→cyber

Eight exact instruction/chat checkpoints were joined between EvalPlus and
Safetywashing. The code-correctness versus cyber behavioral-safety composite
relationship is weakly negative:

- Spearman `ρ = -0.190`;
- Pearson `r = -0.182`;
- family-cluster bootstrap interval `[-0.750, 0.200]`.

All three Mistral/Mixtral checkpoints fall below the pooled descriptive trend,
while both Meta Llama 3 and both Gemma 1.1 checkpoints fall above it. This is a
lineage/alignment signal, not a finding that a family is safe or unsafe. The
panel is too small and confounded to define a release boundary. See
[`../outputs/coding_cyber_exploratory_report.md`](../outputs/coding_cyber_exploratory_report.md).

### Biology and chemistry proxies

On the 26-checkpoint Safetywashing chat panel:

| Pair | Spearman ρ | Family-cluster 95% interval | Interpretation |
|---|---:|---:|---|
| MedQA → WMDP-Bio inverted accuracy | -0.985 | [-0.996, -0.941] | Biomedical knowledge and hazardous biological knowledge rise together |
| MedQA → biochemical HarmBench composite | -0.253 | [-0.677, 0.121] | Behavioral safeguards are largely separate from knowledge capability |
| GPQA → WMDP-Chem inverted accuracy | -0.927 | [-0.981, -0.883] | Broad science capability is associated with greater hazardous chemistry knowledge |

These are construct-level findings, not practical bioweapon or chemical-weapon
uplift estimates. See [`../outputs/domain_proxy_report.md`](../outputs/domain_proxy_report.md).

## Key protocol finding: the score belongs to a profile

For agentic benchmarks, the measured unit must be:

```text
model checkpoint
+ tokenizer/chat template
+ system prompt
+ scaffold and tool permissions
+ sampling and reasoning settings
+ attempt/token/time budget
+ benchmark and judge version
```

SWE-bench Verified contains multiple scores for the same model with different
agents and budgets. Cybench is likewise an agentic, tool-using capability test.
Treating either score as an intrinsic property of the weights would introduce
uncontrolled profile effects into the capability–safety relationship.

## Admissibility decisions

- **Eligible for descriptive analysis:** BenchMIRT broad pairs; Safetywashing
  biomedical/chemistry proxies.
- **Exploratory only:** EvalPlus→cyber safety, because `n=8` even though four
  lineages are represented.
- **Not presently joinable:** SWE-bench→CyberSecEval, LiveCodeBench→current
  cyber safeguards, Cybench→behavioral cyber safety, and all remaining
  specialist domains.
- **Never sufficient alone:** an empirical regression residual. Being above a
  trend does not establish acceptably low absolute risk.

## Stage 1 conclusion

The data support the project's central premise—safety requirements should be
conditioned on activated domain capability—but they also show why the release
rule cannot be learned by fitting one line to public leaderboard points. The
next credible step is a common-harness panel that measures, on the same exact
artifacts:

1. benign domain capability;
2. hazardous capability under strong but bounded elicitation;
3. behavioral safeguards under direct and adversarial prompts;
4. matched benign utility / false refusals;
5. robustness after realistic open-weight modification.

Only that dataset can support a candidate minimum safety envelope and external
validation against consequential-risk anchors.
