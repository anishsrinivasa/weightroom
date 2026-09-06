# Plan: Domain-Conditioned Capability–Safety Meta-Analysis for Open-Weight LLM Release

**Status:** proposed research plan with preliminary baseline analyses
**Scope:** text-only, publicly downloadable open-weight LLMs
**Companion specification:** [`framework.md`](framework.md)
**Prepared:** 2026-09-05

## 1. Objective

Build an evidence base that can:

1. estimate the relationship between model capability and safety within each relevant domain;
2. identify open-weight LLMs whose safeguards underperform relative to similarly capable models;
3. distinguish actual safety behavior from apparent safety caused by model incapability;
4. develop and validate a risk-calibrated minimum safety envelope for unrestricted public-weight release;
5. expose uncertainty, evidence gaps, and benchmark-validity problems rather than concealing them in a composite score.

The primary research question is:

> Conditional on a model's measured capability in domain `d`, does its safety evidence meet both a relative expectation and an independently justified absolute standard for public-weight release?

The included baseline analyses validate the data and reporting pipeline but do
not establish release thresholds. New confirmatory model evaluations should not
begin until this plan is reviewed.

## 2. Claims the study will and will not make

### Intended claims

- A descriptive claim about how each safety measure varies with corresponding capability across the included model population.
- A comparative claim about which models have stronger or weaker safety evidence than comparable models.
- A measurement claim about whether each purported safety benchmark contains signal distinguishable from ordinary capability.
- A policy proposal for a capability-conditioned minimum safety envelope, including the evidence and normative assumptions behind it.

### Claims to avoid

- That correlation proves capability causes safety or unsafety.
- That being above an empirical regression line proves a model is safe.
- That a single scalar can represent all forms of safety.
- That refusal in the submitted chat template survives open-weight modification.
- That missing public benchmark results imply either safety or failure.
- That certification warrants safety in every downstream system or future fine-tune.

## 3. Conceptual model

For model artifact `i`, domain `d`, and safety construct/test `j`, retain separate measurements:

- `C_id`: benign or productive capability in domain `d`;
- `H_id`: hazardous capability under strong elicitation;
- `S_ij`: behavioral safety or safeguard robustness on test `j`;
- `U_ij`: benign utility / false-refusal behavior paired with `j`;
- `M_idb`: safety and hazardous capability after modification budget `b`;
- `Z_i`: artifact and evaluation covariates such as family, size, base/instruct status, release date, quantization, scaffold, and protocol.

The descriptive relationship is:

```text
expected_safety_ij = m_j(C_id, Z_i)
safety_gap_ij      = observed_safety_ij - expected_safety_ij
```

The empirical relationship identifies unusual underperformance. It will not define acceptable risk. A separate, predeclared minimum safety envelope will be developed:

```text
required_safety_j = tau_j(C_id, release_context)
```

For metrics where larger values mean more danger, their semantic meaning will be retained and the decision inequality reversed. Raw scores will never be relabeled without preserving the original metric and direction.

## 4. Unit of analysis and eligibility

The unit of analysis is an **exact model artifact under an exact evaluation profile**, not a marketing model name.

### Model inclusion criteria

- Weights are publicly downloadable or were publicly downloadable at the recorded date.
- The model is text-input/text-output; code and text-tool use are allowed.
- The exact checkpoint, revision, tokenizer, and chat template can be resolved.
- At least one eligible capability/safety pair is available under an auditable protocol, or the artifact can be run within project resources.
- License and access terms permit the intended evaluation and storage of derived scores.

### Exclusions

- API-only models from the primary analysis. They may be retained as contextual frontier anchors in clearly separate plots.
- VLM-only scores, visual prompt injection, image-dependent benchmark subsets, GUI agents, audio, and robotics.
- Scores that cannot be tied to an exact checkpoint or whose metric/protocol cannot be reconstructed.
- Self-reported aggregate scores without enough protocol information for comparability; these may be recorded as low-confidence background evidence but not used to fit a release boundary.

### Required artifact identity fields

- canonical artifact ID and cryptographic digest where available;
- repository and revision;
- declared base lineage and adapter/fine-tune relationship;
- architecture, parameter count, quantization, and context length;
- base, pretrained, instruct, chat, safety-tuned, or refusal-removed class;
- tokenizer and chat-template versions;
- release date, developer organization, and model family;
- system prompt, scaffold, tools, sampling, attempt budget, and judge used for each score.

Base and instruction-tuned models will be modeled separately and jointly only with an explicit model-class term. Related checkpoints will remain grouped by family and organization during inference.

## 5. Domain and benchmark map

This is the initial evidence map from the companion framework. The source audit may add, retire, or downgrade benchmarks, but every change will be recorded with a reason.

| Domain | Capability evidence | Corresponding safety / hazardous evidence | Initial analysis readiness |
|---|---|---|---|
| General reasoning and knowledge | MMLU-Pro, GPQA, private/compact general router | AILuminate T2T, HarmBench, StrongREJECT, JailbreakBench attacks, XSTest controls | High for public-data construct analysis; only moderate for release-risk inference |
| Instruction following and function calling | IFEval, BFCL | instruction-hierarchy conflicts, jailbreak attacks, unsafe arguments, matched false refusal | Moderate; protocols and scaffolds require harmonization |
| Software engineering → cyber | SWE-bench Live or SWE-Bench Pro; SWE-bench Verified as secondary evidence; LiveCodeBench/BigCodeBench | CyberSecEval 4 insecure-code and MITRE/FRR tracks; Cybench; held-out cyber tasks | Highest-priority complete vertical; public overlap must be measured |
| Cybersecurity | Cybench, CTI reasoning, sandboxed offensive and defensive tasks | CyberSecEval 4, strongly elicited Cybench, private dual-use tasks, jailbreak variants | Moderate to high; keep capability, harmful compliance, and defensive utility separate |
| Biology and biotechnology | LAB-Bench text subsets, GPQA biology | WMDP-Bio proxy, expert uplift tasks, jailbreak and bounded fine-tuning | Moderate for knowledge proxies; low for consequential-risk conclusions without experts |
| Chemistry | ChemBench, GPQA chemistry | WMDP-Chem, ChemSafetyBench, expert acquisition/synthesis/troubleshooting tasks | Moderate for public proxies; low for real-world release inference without experts |
| Medicine and health | HealthBench, held-out clinical reasoning | high-acuity, contraindication, dosage, uncertainty, escalation, self-harm and false-reassurance tests | Moderate; separate clinical correctness from safe behavior and jurisdictional claims |
| Text agents and tool use | BFCL, tau-bench, text-only GAIA | AgentHarm, AgentDojo, privilege, exfiltration, unsafe side effects | Moderate; strongly scaffold-dependent |
| Long-horizon autonomy and AI R&D | RE-Bench, MLE-bench, hard repository tasks, METR time horizons | sabotage, oversight evasion, reward hacking, replication/exfiltration | Low for public meta-analysis; likely a structured evidence-gap report plus new evaluation |
| Persuasion and manipulation | controlled belief-shift and persuasion evaluations | harmful targeting, coercion, impersonation, fraud, vulnerable-population tasks | Low; heterogeneous human-subject protocols and limited open-weight overlap |
| Finance | FinanceBench and filing/reasoning tasks | financial-advice hazards, fraud, manipulation, uncertainty and suitability | Low to moderate; likely claim-conditioned rather than universal catastrophic-risk gate |
| Law | LegalBench and jurisdiction-specific reasoning | fabricated citation, deadline, confidentiality, unauthorized-practice and uncertainty tests | Low to moderate; expert and jurisdiction effects are large |
| Privacy and data handling | retrieval/document QA, long-context extraction | PrivacyLens, canaries, cross-user leakage, sensitive inference, prompt injection/exfiltration | Moderate for behavioral privacy; training-data memorization requires different evidence |
| Multilingual and cross-cultural | Global MMLU, MMLU-ProX or native-language equivalents | XSafety, MultiJail, XL-SafetyBench, activated-domain tests in supported languages | Moderate; native-language protocols must not be collapsed into translated English |
| Long-context and RAG | RULER, LongBench text subsets, grounded QA | indirect prompt injection, poisoned retrieval, confidential context, provenance | Moderate; results bind to a tested scaffold, not the bare weights alone |

### Domain-pair admissibility

A capability/safety pair will enter confirmatory analysis only if:

1. there is a defensible causal or functional reason for the capability to enable the tested harm;
2. the safety construct is explicitly named—e.g. harmful compliance, secure coding, prompt-injection robustness—not merely called “safety”;
3. the model overlap includes at least 12 artifacts from at least four substantially independent model families or organizations;
4. protocol variation can be controlled or scores can be rerun under a common protocol;
5. score direction, range, sampling variance, and judge are known.

Pairs with 6–11 eligible artifacts will be labeled exploratory. Pairs with fewer than 6, fewer than three independent families, or irreconcilable protocols will be reported as not presently estimable. These sample-size rules are provisional and will be checked through simulation-based power analysis before preregistration.

## 6. Evidence acquisition strategy

Use a source hierarchy:

1. official benchmark result files or benchmark-native leaderboards with exact model revisions;
2. peer-reviewed papers and their released machine-readable data;
3. model system cards with reconstructable protocols;
4. independent evaluator reports with exact configurations;
5. our own reruns under a pinned common harness;
6. seller/model-card claims only as unverified leads.

### Seed sources already identified

- *Safetywashing* model/benchmark matrices and analysis code;
- BenchMIRT's 100-open-weight-model benchmark panel;
- Open LLM and benchmark-native leaderboards where exact artifacts can be resolved;
- HarmBench, StrongREJECT, JailbreakBench, AILuminate and XSTest result sources;
- CyberSecEval 4 and Cybench result sources;
- LAB-Bench, WMDP, ChemBench/ChemSafetyBench and HealthBench sources;
- AgentHarm, AgentDojo, BFCL, tau-bench, GAIA, RULER and LongBench sources;
- public system cards and responsible-scaling evaluations used only where protocols are sufficiently specified.

### Source ledger

Every imported observation will include:

- source URL, title, authors/owner, publication date, access date, and license;
- table, figure, file, row, or commit from which it came;
- whether the score is provider-reported, benchmark-maintainer-run, independent, or reproduced by us;
- exact metric definition and direction;
- model identity confidence;
- protocol comparability class;
- any extraction or transformation applied;
- known contamination, saturation, judge, or sample-size limitations.

No safety-sensitive held-out prompt content will be stored in the public repository. Restricted tasks will be represented by metadata and aggregate results only.

## 7. Data model

Use a long-format observation table rather than a wide spreadsheet as the authoritative analytical dataset.

```text
models
  artifact_id, digest, repo, revision, family, organization,
  parameter_count, architecture, model_class, release_date, license

benchmarks
  benchmark_id, version, domain, construct, role,
  score_direction, scale, modality, public_private, native_protocol

evaluation_profiles
  profile_id, harness, template, system_prompt_hash, scaffold,
  tools, sampling, attempts, token_budget, judge, run_date

observations
  artifact_id, benchmark_id, profile_id, source_id,
  raw_score, normalized_display_score, standard_error,
  n_items, confidence, comparability_class

lineage
  child_artifact_id, parent_artifact_id, transformation_type

modification_runs
  artifact_id, domain, modification_type, budget,
  capability_before_after, safety_before_after
```

Raw source values remain immutable. Corrections create new versioned records rather than overwriting history.

## 8. Protocol harmonization

### Do not naïvely pool leaderboard scores

Scores will be considered directly comparable only when all material protocol elements match. Otherwise, use one of these strategies in order:

1. rerun the models under a common pinned protocol;
2. restrict the analysis to a comparable protocol stratum;
3. include protocol as a modeled effect when sufficient overlap identifies it;
4. retain the observation descriptively but exclude it from fitted relationships.

### Score semantics

Each safety observation will be typed as one of:

- `safe_behavior` — principled refusal or safe redirection;
- `harmful_compliance` — useful assistance toward a harmful objective;
- `hazardous_capability` — ability to complete a consequential task;
- `secure_output` — absence of security-critical flaws;
- `adversarial_robustness` — safety under attacks or hostile context;
- `false_refusal` — refusal of benign or defensive requests;
- `privacy_preservation` — non-disclosure or correct information-flow behavior;
- `judge_or_monitor_accuracy` — performance of a safeguard component, not the target model itself.

Incoherent, irrelevant, truncated, and invalid outputs must be separately labeled. They cannot automatically count as safe refusals.

### Capability composites

The primary analyses will use domain-specific capability measurements, not one universal capability score. When several measures exist in a domain:

- report every benchmark-specific relationship;
- estimate a domain latent factor only when overlap and construct analysis support it;
- compare PCA/factor/IRT alternatives through held-out predictive validity;
- preserve a single-benchmark analysis so the result is interpretable;
- do not allow a general reasoning composite to substitute for demonstrated cyber, bio, or agentic capability.

## 9. Statistical analysis

### 9.1 Descriptive layer

For every admissible capability/safety pair:

- plot every exact artifact with uncertainty intervals where available;
- visually distinguish base, instruct, safety-tuned, and refusal-removed models;
- group related artifacts by family and organization;
- show sample size, missingness, source type, and protocol stratum;
- report Spearman, Kendall, and Pearson correlations with cluster-aware intervals;
- show raw score orientation and a clearly labeled safety-oriented view;
- identify saturation, floor/ceiling effects, and influential observations.

### 9.2 Relationship model

The default confirmatory model will be a hierarchical errors-in-variables regression:

```text
g_j(S_ij) = alpha_j
           + f_j(C_id)
           + model_class_effect
           + release_date_effect
           + protocol_effect
           + family_random_effect
           + organization_random_effect
           + error_ij
```

Where `g_j` respects the metric distribution and `f_j` is initially linear but may become a monotone spline when preregistered diagnostics show nonlinearity. Both x- and y-axis measurement uncertainty will be modeled when estimable.

Robustness analyses will include:

- family- and organization-cluster bootstraps;
- leave-one-family and leave-one-organization-out estimates;
- base-only and instruct-only strata;
- public-source-only and common-harness-only strata;
- robust regression and rank-based estimates;
- alternative capability composites;
- nonlinear and change-point specifications;
- exclusion of saturated or contamination-flagged benchmarks;
- correction for multiple pairwise analyses using false-discovery-rate control.

### 9.3 Missingness

Benchmark coverage will be missing-not-at-random because developers selectively publish favorable scores. Therefore:

- do not use simple mean imputation;
- publish the model-by-benchmark missingness matrix;
- compare complete-case and common-harness subsets;
- model publication/source selection when evidence supports it;
- use matrix completion only as a sensitivity analysis, never as certification evidence;
- prioritize rerunning strategically selected models that break family, size, or safety-tuning confounds.

### 9.4 Safety-underperformance flag

For each model/test, estimate the posterior or bootstrap distribution of:

```text
safety_gap_ij = observed_safety_ij - expected_safety_ij
```

Flag **relative safeguard underperformance** only when:

```text
P(safety_gap_ij < -delta_j) >= 0.95
```

`delta_j` is a benchmark-specific minimum practically important difference set before inspecting model identities. A raw point below the fitted mean is not enough. Report the gap, uncertainty, reference population, and sensitivity to family/protocol controls.

This flag means “unusually weak safeguards relative to measured capability,” not “unsafe to release.”

## 10. Open-weight modification analysis

Behavioral safety in an instruct checkpoint is mutable. For sufficiently capable models, evaluate a bounded modification envelope:

1. submitted chat configuration;
2. neutral or template-stripped completion;
3. common quantization variants where behavior may change;
4. bounded refusal-removal fine-tuning;
5. bounded domain-capability fine-tuning for cyber and bio/chem only under controlled review;
6. strong elicitation with fixed best-of-k, reasoning, scaffold, and tool budgets.

Plot both safety and hazardous capability against modification/attacker budget. Record:

- safety degradation rate;
- hazardous-capability gain;
- budget at which a consequential threshold is crossed;
- uncertainty and run-to-run variance;
- whether the submitted model increases the public frontier beyond already available artifacts.

The fine-tuning protocol, not sensitive examples, should be externally reviewable. High-risk task assets remain controlled.

## 11. Constructing the minimum safety envelope

The study will keep two lines conceptually and visually separate.

### Empirical expectation line

`m_j(C, Z)` describes what comparable models currently do. Falling materially below it triggers investigation. It must not become the release standard because an unsafe reference population would create an unsafe standard.

### Normative release envelope

`tau_j(C, X)` defines the minimum acceptable evidence for release context `X`. For this study, unrestricted public weights are the least controllable context.

The envelope will be constructed in four steps:

1. **Define the harm model.** Specify threat actor, harmful pathway, consequence severity, exposure, existing alternatives, and available defenses per domain.
2. **Set absolute anchors.** Predeclare critical-event rules, maximum hazardous-task success or uplift, minimum benign utility, and minimum safeguard robustness using expert and stakeholder review.
3. **Condition assurance on capability.** Increase test coverage, attack budget, required lower confidence bound, and modification testing at C1/C2/C3 capability levels. Avoid assuming all score scales are interval-comparable.
4. **Validate and stress-test.** Apply the proposed envelope to anchor models, sibling checkpoints, held-out model families, and independently evaluated releases; examine false certification and unnecessary withholding.

The default envelope will be piecewise by capability level rather than an arbitrary straight line:

| Capability level | Minimum evidence |
|---|---|
| C0 — not demonstrated | universal safety battery; domain test still runs if intended-use or tripwire trigger fires |
| C1 — basic | public domain safety test plus matched benign controls |
| C2 — operational | public and held-out safety, adversarial robustness, uncertainty margin, and utility evidence |
| C3 — consequential | expert review, maximum elicitation, bounded modification, hazardous-capability limit, and explicit risk case |

### Candidate release rule

For every activated domain `d`, unrestricted release is eligible only if:

```text
lower_confidence_bound(behavioral_safety_d) >= tau_d(capability_d)
AND upper_confidence_bound(hazardous_capability_d) < kappa_d
AND modification_requirement_d passes
AND no unresolved critical event exists
AND evidence quality is adequate
```

The cross-domain result is conjunctive: one failed critical domain cannot be averaged away by strong unrelated capability or safety scores.

Possible outcomes:

- `ELIGIBLE` — all applicable domain cases pass;
- `REVIEW` — boundary proximity or a critical event requires expert adjudication;
- `WITHHOLD_UNRESTRICTED_WEIGHTS` — an applicable absolute requirement fails;
- `INSUFFICIENT_EVIDENCE` — required evidence is missing, invalid, or underpowered.

## 12. Calibration and validation

### Anchor panels

Construct panels containing:

- weak and strong models across several independent families;
- base/instruct siblings;
- safety-tuned and refusal-removed siblings;
- code- and science-specialized models;
- models previously evaluated by credible independent organizations;
- models intentionally held out until the envelope is frozen.

### Validation questions

- Does the model correctly distinguish refusal from incompetence?
- Does it identify known safeguard removals within a family?
- Does the underperformance flag persist after family, organization, and protocol controls?
- Does the envelope generalize to held-out model families and later releases?
- Does it agree with blinded expert adjudication on critical cases?
- Is it robust to plausible judge error, benchmark contamination, and score orientation?
- Does it improve prediction of held-out red-team outcomes beyond capability alone?
- How often would it unnecessarily withhold low-risk models?
- How often would it certify models that later fail stronger attacks or modification tests?

### External review

Before using the envelope as a real publication gate:

- obtain statistical review of the model and multiplicity handling;
- obtain domain-expert review for cyber, biology, chemistry, medicine, and other high-stakes domains;
- preregister the confirmatory relationships and envelope calibration procedure;
- publish the non-sensitive methods and known limitations;
- commission or invite an independent reproduction on a subset of models.

## 13. Planned outputs

### Evidence artifacts

- versioned source ledger and benchmark catalog;
- canonical model/artifact registry and lineage graph;
- immutable raw-observation records with provenance;
- normalized analytical dataset with an explicit data dictionary;
- model-by-benchmark coverage and comparability matrix.

### Analysis artifacts

- one capability–safety plot per admissible benchmark pair;
- domain dashboards showing raw, within-family, and modification-aware relationships;
- correlation and hierarchical-model tables with uncertainty;
- relative safeguard-underperformance table;
- benchmark construct-validity and sensitivity report;
- evidence-gap report for non-estimable domains.

### Policy artifacts

- proposed domain-specific minimum safety envelopes;
- absolute hazardous-capability and critical-event rules;
- release decision table with `ELIGIBLE`, `REVIEW`, `WITHHOLD`, and `INSUFFICIENT_EVIDENCE` outcomes;
- redacted public certificate design and internal safety-case template;
- integration recommendations for Keystone's evaluation planner and final publication gate.

### Reproducibility artifacts

- pinned environment and evaluation-profile manifests;
- scripts/notebooks that regenerate tables and plots from permitted data;
- tests for score direction, artifact identity, missing required results, and release-rule invariants;
- a frozen analysis release with hashes and methodology version.

## 14. Target workspace layout

```text
capabilities-vs-safety-eval/
  README.md
  methodology.md
  schemas/
  catalog/
    models.yaml
    benchmarks.yaml
    sources.yaml
  data/
    raw/                 # permitted public source files; immutable
    processed/           # normalized observations
    restricted-metadata/ # no sensitive prompts
  analysis/
    coverage.py
    construct_validity.py
    relationships.py
    underperformance.py
    envelope.py
    sensitivity.py
  outputs/
    figures/
    tables/
    reports/
  tests/
```

## 15. Execution stages and review gates

### Gate 1 — approve the research plan

Confirm scope, terminology, domain priorities, admissibility rules, and the separation between empirical expectation and normative release standard.

### Stage 1 — systematic source and coverage audit

- search benchmark papers, repositories, leaderboards, system cards, and evaluator reports;
- resolve exact artifacts and protocols;
- create the source ledger and missingness matrix;
- report which domain pairs meet confirmatory, exploratory, or insufficient-evidence criteria.

**Gate 2:** review the evidence map before expensive reruns.

### Stage 2 — common-harness gap filling

- select models to maximize family, size, and tuning diversity;
- rerun only the benchmark pairs needed to resolve important overlap/confounding gaps;
- validate judges and score transformations;
- freeze the confirmatory analysis specification.

**Gate 3:** approve the preregistered statistical analysis and compute budget.

### Stage 3 — relationship estimation

- create plots and descriptive statistics;
- fit hierarchical and robustness models;
- identify relative safeguard underperformance;
- publish construct-validity and evidence-gap findings.

**Gate 4:** review findings before defining release thresholds, preventing threshold selection around favored models.

### Stage 4 — envelope development

- develop threat models and absolute anchors;
- calibrate piecewise capability-conditioned requirements;
- add maximum-elicitation and modification evidence;
- backtest on anchor models and validate on held-out families.

**Gate 5:** independent statistical and domain review.

### Stage 5 — policy and Keystone integration proposal

- freeze a versioned release policy;
- specify planner, schema, reporting, and final-gate changes;
- implement only after separate authorization;
- shadow-test before any real listing is blocked or approved by the new policy.

## 16. Primary risks and mitigations

| Risk | Mitigation |
|---|---|
| Safety benchmark actually measures capability | construct-validity analysis, incoherence labels, latent-factor and partial-correlation tests |
| Selective reporting / missing-not-at-random | source hierarchy, coverage matrix, strategic common-harness reruns, no naïve imputation |
| Model-family pseudo-replication | family and organization grouping, cluster bootstrap, leave-group-out validation |
| Incompatible protocols | strict comparability classes, reruns, protocol effects, descriptive-only exclusions |
| Benchmark contamination or saturation | freshness audit, live/held-out alternatives, sensitivity exclusions |
| Judge bias and correlated errors | diverse judges, expert calibration, disagreement review, recorded judge error |
| Empirical line becomes an unsafe norm | keep descriptive expectation and normative envelope separate |
| Average hides severe events | critical-event veto/hold rules and domain-conjunctive adjudication |
| Refusal is removed after download | template-stripped and bounded-modification evaluations |
| Sensitive evaluation leakage | controlled storage, aggregate public reporting, no sensitive prompts in this repository |
| Threshold overfitting | preregistration, held-out families, frozen policy versions, independent review |

## 17. Definition of done

The research objective is complete only when:

1. all fourteen domain rows have a documented evidence audit;
2. every included score is tied to an exact artifact, source, metric, and protocol;
3. every admissible pair has a reproducible plot and uncertainty-aware relationship estimate;
4. non-estimable domains are explicitly reported with the evidence needed to make them estimable;
5. relative safeguard-underperformance findings survive the specified sensitivity analyses or are labeled unstable;
6. empirical expectation lines are clearly separated from normative release envelopes;
7. proposed envelopes include absolute risk anchors, hazardous-capability limits, critical-event rules, and open-weight modification requirements;
8. envelopes are backtested and evaluated on held-out model families;
9. statistical and relevant domain experts review the methodology;
10. all non-sensitive analysis artifacts can be reproduced from the frozen source ledger and environment;
11. a concrete, versioned Keystone integration proposal is delivered;
12. no model is represented as “safe” solely because it lies above a peer-derived regression line.

## 18. Decisions requested before Stage 1

Recommended defaults are shown below.

1. **Primary model population:** open-weight text LLMs only; use API models solely as separate contextual anchors.
2. **Initial vertical:** complete coding → cyber first while auditing all fourteen domains in parallel at the source level.
3. **Evidence standard:** public data first, then targeted common-harness reruns to repair the most decision-relevant coverage gaps.
4. **Release interpretation:** use the empirical line only for relative underperformance; use a separately calibrated normative envelope for release.
5. **High-risk evidence:** require external domain experts before setting bio, chemistry, cyber-C3, or autonomy thresholds.
