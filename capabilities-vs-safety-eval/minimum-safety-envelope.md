# Risk-Calibrated Minimum Safety Envelope for Public-Weight LLM Release

**Status:** implementable candidate specification; numerical risk tolerances
require external calibration and expert review
**Scope:** text-only open-weight LLMs
**Important:** this document does not classify any model as safe or unsafe

## Decision in one sentence

A public-weight release should pass only when every activated high-risk domain
has sufficient evidence, hazardous capability remains below a risk-calibrated
ceiling, behavioral safeguards and benign utility exceed absolute floors,
safety does not materially underperform capability-conditioned expectations,
and the result survives a realistic modification audit.

An empirical regression line is only one diagnostic inside this rule. It is not
the safety boundary.

## Why correlation cannot directly determine release

The current analyses show three different regimes:

- Broad reasoning has different relationships with different safety tests.
- Biomedical/science capability is very strongly associated with more hazardous
  WMDP knowledge, even though behavioral refusal varies much more independently.
- Coding→cyber behavioral safety is uncertain and strongly confounded by model
  lineage in the small public overlap panel.

Therefore, correlation answers **whether a safety metric adds information beyond
capability**. It does not answer **how much harm society should tolerate**.
Those are separate quantities:

```text
evidential weight = reliability × construct validity × capability-distinctness
policy strictness  = plausible harm severity × exposure × misuse feasibility
```

A highly capability-correlated “safety” benchmark should usually receive less
weight as independent evidence of safeguards. A domain with severe harms should
receive a stricter gate even if its public benchmarks are noisy.

## Measurements retained for each model and domain

For exact model artifact `i` and domain `d`:

- `C_id`: benign/productive domain capability;
- `H_id`: hazardous capability under strong, bounded elicitation;
- `S_idj`: behavioral safeguard result on safety test `j`;
- `U_idj`: matched benign utility or false-refusal control;
- `G_idj`: capability-conditioned safety residual;
- `M_idb`: hazardous capability and safeguards after modification budget `b`;
- `Q_id`: evidence-quality and coverage status;
- `Z_i`: lineage, size, model class, quantization, prompt, scaffold, tools,
  inference settings, attempt budget, and judge.

All scores retain their original semantics. Higher-is-dangerous scores are not
silently inverted in stored data; an oriented copy may be created for a plot.

## Domain activation

Safety evaluation is conditional on demonstrated capability, but absence of a
public score is never treated as absence of capability.

Define three activation levels using held-out, benchmark-specific anchors:

| Level | Meaning | Required evidence |
|---|---|---|
| A0: not demonstrated | Below benign capability activation and no contrary evidence | Evidence-quality check and a compact domain screen |
| A1: materially useful | Can complete meaningful benign work in the domain | Full behavioral safety and benign-utility battery |
| A2: hazardous enablement | Demonstrates expert-relevant or end-to-end hazardous capability, or approaches an uplift anchor | Strong elicitation, expert review, modification audit, and risk-model calibration |
| A3: frontier dangerous | Crosses a predeclared unacceptable hazardous-capability or uplift threshold | No public-weight release absent an independently justified exceptional process |

Activation must use a lower confidence bound on capability. A model activates a
domain when `LCB(C_id) >= a_d`. A credible external result, model card claim, or
lineage signal can trigger evaluation even when the project's compact screen is
below threshold.

## Risk calibration

For each domain, define concrete misuse scenarios `k`. Estimate:

```text
R_idk = P(access) × P(attempt | access) ×
        P(successful harm | attempt, H_id, S_id, M_id) × Loss_dk
```

For public weights, `P(access)` is effectively 1 and removable behavioral
safeguards cannot be assumed to remain intact. Calibration should therefore be
based on the strongest realistic elicitation/modification profile, not the
default chat refusal rate.

The hazardous-capability ceiling `κ_d` is the largest score whose upper
confidence bound maps below the predeclared risk tolerance `ε_d`:

```text
κ_d = sup { h : UCB(calibrated_risk_d(h)) < ε_d }
```

Calibrate `calibrated_risk_d(h)` using held-out consequential anchors, such as
expert task success, novice/expert uplift, end-to-end sandboxed task completion,
or another outcome with a defensible connection to harm. WMDP, HarmBench, and
multiple-choice accuracy alone cannot supply this mapping.

## Capability-conditioned underperformance line

For each safety test `j`, fit an empirical relationship on a reference panel:

```text
expected_safety_ij = m_j(C_id, Z_i)
gap_ij             = S_ij - expected_safety_ij
```

Use a hierarchical monotonic or spline model where sample size permits, with
partial pooling by lineage and separate terms for base/instruct/reasoning model
class. With smaller panels, report rank correlations, robust regression, and
leave-one-lineage-out sensitivity rather than a precise curve.

The relative anti-regression check is:

```text
LCB(G_idj) >= -δ_dj
```

where `δ_dj` is calibrated on a held-out panel of known safety interventions
and deliberately weakened/abliterated controls. This catches a model whose
safeguards are unusually weak for its capability. It cannot rescue a model that
fails an absolute floor.

## Weighting benchmark evidence

For estimation and dashboards, assign test `j` an evidential weight:

```text
D_j = 1 - clamp(partial_R2(S_j ~ C + lineage + model_class), 0, 1)
w_j ∝ Rel_j × Val_j × D_j × Coverage_j / Uncertainty_j
```

where:

- `Rel_j` is test/retest and judge reliability;
- `Val_j` is expert-rated construct and consequential validity;
- `D_j` is incremental information beyond capability;
- `Coverage_j` measures scenario and attack coverage;
- `Uncertainty_j` includes sampling, judge, and protocol uncertainty.

Normalize weights only within a named construct. Never allow a strong score on
one construct to compensate for failure on another. In particular:

- hazardous knowledge does not compensate for refusal robustness;
- refusal robustness does not compensate for end-to-end hazardous capability;
- harmless refusal does not compensate for excessive refusal of benign tasks;
- a general safety average does not compensate for failure in an activated
  high-risk domain.

The current public estimates imply:

| Evidence type | Current treatment |
|---|---|
| WMDP-Bio/Chem/Cyber | Useful hazardous-knowledge suppression proxy; strong capability dependence; never sufficient alone |
| HarmBench domain slices | Independent behavioral-safeguard evidence; require matched benign controls and multiple attacks |
| EvalPlus→cyber composite | Exploratory only because `n=8` and lineage confounding is severe |
| BenchMIRT broad safety average | Useful diagnostic summary; no direct release weight |
| SWE-bench/Cybench leaderboard rows | Profile-level capability evidence; no bare-model merge without fixed-profile reruns |

## Candidate release rule

For every activated domain `d`, require all applicable conjuncts:

```text
DOMAIN_PASS(i, d) =
    EVIDENCE_PASS(Q_id)
    AND UCB(H_id) < κ_d
    AND for every critical safeguard j:
            LCB(S_idj) >= τ_abs_dj
            AND LCB(G_idj) >= -δ_dj
            AND LCB(U_idj) >= u_min_dj
    AND MODIFICATION_PASS(M_idb, b_d)
```

The public-weight decision is:

```text
PUBLIC_WEIGHT_RELEASE(i) =
    ARTIFACT_AND_LICENSE_PASS(i)
    AND all activated domains pass
    AND no A3 domain is present
    AND no critical evidence is missing
```

This is deliberately non-compensatory. The release result is not a weighted
average across domains.

## Risk-tier requirements

| Domain tier | Examples | Absolute requirements | Correlation role |
|---|---|---|---|
| Critical | biological misuse, chemical misuse, advanced cyber, autonomous replication/AI R&D | hazardous ceiling, expert/consequential anchor, adversarial safeguards, benign controls, modification test | Detect underperformance and benchmark redundancy; cannot relax floors |
| High | medicine, consequential agents, privacy/exfiltration, targeted persuasion/fraud | severe-case strata, calibrated abstention/escalation, adversarial test, utility control, profile-bound evaluation | Adjust evidence weight and prioritize residual audits |
| Elevated | finance, law, multilingual harms, RAG injection | domain-specific floors, uncertainty disclosure, matched benign utility, deployment assumptions | Identify capability-linked metrics and language/profile gaps |
| General | broad harmful content and instruction following | broad harm battery, jailbreak robustness, XSTest-style over-refusal control | Baseline diagnostic; never substitutes for activated domain tests |

## Modification audit for open weights

Behavioral safeguards in a chat checkpoint are not a durable barrier once the
weights are public. For A2 domains and critical domains, test at least:

- system-prompt and chat-template removal;
- common jailbreaks and long-context attacks;
- low-rank fine-tuning or refusal-removal within a predeclared compute/data
  budget;
- quantization and alternate inference templates;
- scaffolded/tool-using elicitation with a bounded attempt/token/time budget.

Record both the lowest safeguard score and highest hazardous capability reached
within budget. `MODIFICATION_PASS` must be based on upper risk bounds after
modification, not on average default behavior.

## Evidence sufficiency gate

An activated domain is **not evaluable** rather than passing when any of these
are absent:

- exact checkpoint and revision;
- score direction, sample count, and uncertainty;
- prompt/scaffold/tool and judge versions;
- at least two meaningfully different attacks for adversarial safeguards;
- matched benign utility controls;
- an applicable hazardous-capability or uplift measure for critical domains;
- modification evidence for A2/critical public-weight candidates.

“Not evaluable” defaults to no public-weight release until evidence is supplied.
It is not a claim that the model is intrinsically unsafe.

## Calibration protocol

1. **Predeclare scenarios and tolerances.** Domain experts and governance owners
   specify harms, affected populations, exposure assumptions, and `ε_d` before
   viewing candidate-model results.
2. **Build anchor panels.** Include weaker/stronger base models, aligned
   checkpoints, refusal-removed or abliterated variants, targeted safety-tuned
   variants, and agent scaffolds.
3. **Collect common-profile measurements.** Use the same artifacts, inference
   stack, and item sets across capability, hazardous capability, safeguards,
   and utility.
4. **Fit calibration and expectation models.** Use nested cross-validation with
   lineage-held-out folds; preserve item-level uncertainty.
5. **Choose thresholds on training folds only.** Set `κ`, `τ_abs`, and `δ` to
   satisfy risk tolerances and a predeclared false-negative bound.
6. **Validate prospectively.** Freeze the rule, evaluate unseen model lineages,
   and measure whether known weakened controls are rejected and useful safe
   controls are not trivially rejected.
7. **Red-team the release rule.** Test metric gaming, benchmark contamination,
   fine-tuning attacks, judge manipulation, and profile substitution.
8. **Obtain external review.** Critical-domain thresholds require independent
   cyber/bio/chemistry experts and governance review.

## Minimum model panel for the first operational calibration

For each critical domain, target at least 24 exact artifacts from at least six
substantially independent lineages, including:

- at least six base/pretrained checkpoints;
- at least six standard instruct/chat checkpoints;
- at least four deliberately weakened/refusal-removed controls;
- at least four safety-tuned controls;
- at least four contemporary high-capability models;
- multiple sizes within at least three lineages.

The earlier 12-model/four-lineage criterion remains the minimum for a
confirmatory relationship estimate. It is not enough for a release-calibration
study with modification and model-class interactions.

## Output classifications

Use four outcomes:

| Outcome | Meaning |
|---|---|
| Public-weight eligible | All activated-domain gates pass with adequate evidence under the declared release context |
| Conditional/restricted distribution | Risk may be manageable only with access controls, monitoring, or a non-weight deployment boundary |
| Not presently evaluable | Critical evidence is missing, incomparable, or too uncertain |
| Public-weight ineligible | A hazardous ceiling, critical absolute floor, A3 trigger, or modification gate fails |

The output must list the exact failed or missing conjuncts. Do not publish only
a scalar “safety score.”

## What can be implemented now

- Use the current scripts to generate descriptive relationships and residual
  audit queues.
- Treat models below a capability-conditioned line as priority candidates for
  deeper evaluation, not automatic rejection.
- Use the domain activation matrix to determine which batteries are mandatory.
- Refuse to fit an operational release boundary until a common-harness panel and
  consequential-risk anchors exist.

The public data support this structure, but not numerical claims that a model
is safe for public-weight release.
