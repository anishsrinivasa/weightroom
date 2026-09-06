# Capability-Conditioned Safety Assurance for Open-Weight LLMs

**Status:** implementation proposal
**Scope:** text-only large language models distributed as downloadable weights
**Prepared for:** Weightroom / Keystone
**Last reviewed:** 2026-09-05

## Executive decision

Weightroom should adopt **capability-conditioned safety assurance**: every model receives a low-cost, mandatory capability-routing evaluation; demonstrated capability, claimed use, or direct risk signals then activate more demanding safety evaluations in the corresponding domain.

The underlying idea is not novel. It is a practical synthesis of risk-tiering, capability thresholds, dangerous-capability evaluations, safeguard evaluations, and responsible scaling policies. The closest direct precedent is the European Commission Joint Research Centre's 2025 proposal for a **dual-trigger framework** combining capability triggers with safety benchmarks. Frontier-lab policies likewise use capability thresholds to activate stronger safeguards. What can be distinctive here is the implementation for a third-party, open-weight marketplace: independent certification, automatic domain routing, seller-proof benchmark selection, held-out evidence, and an explicit modification-resilience requirement.

The most important policy conclusion is:

> For downloadable weights, a high score on refusal benchmarks is not sufficient evidence of safety. If a model exhibits consequential hazardous capability after plausible elicitation or modification, Weightroom should not certify it for unrestricted public distribution unless the risk is reduced at the capability level and remains reduced under a predefined modification test.

This follows because downstream users can remove system prompts, replace chat templates, run unfiltered decoding, and fine-tune the weights. Controls appropriate for an API—rate limits, identity checks, monitoring, and server-side filters—do not travel with the artifact.

## 1. What the literature supports

This proposal combines several established ideas:

1. **Separate capability from propensity and safeguards.** Shevlane et al.'s [Model Evaluation for Extreme Risks](https://arxiv.org/abs/2305.15324) distinguishes dangerous capabilities from a model's propensity to use them. A benign-domain capability score and a refusal score therefore answer different questions and should not be averaged.
2. **Scale assurance with risk.** The UK government's [Emerging Processes for Frontier AI Safety](https://www.gov.uk/government/publications/emerging-processes-for-frontier-ai-safety/emerging-processes-for-frontier-ai-safety), the [Seoul Frontier AI Safety Commitments](https://www.gov.uk/government/publications/frontier-ai-safety-commitments-ai-seoul-summit-2024/frontier-ai-safety-commitments-ai-seoul-summit-2024), Anthropic's [Responsible Scaling Policy](https://www.anthropic.com/news/announcing-our-updated-responsible-scaling-policy), OpenAI's [Preparedness Framework](https://cdn.openai.com/pdf/18a02b5d-6b67-4cec-ab64-68cdfbddebcd/preparedness-framework-v2.pdf), and Meta's [Advanced AI Scaling Framework](https://ai.meta.com/static-resource/Meta_Advanced-AI-Scaling-Framework-v2) all use capability levels or thresholds to trigger additional evaluation and safeguards.
3. **Use two ways to enter a safety evaluation.** The JRC report, [The Role of AI Safety Benchmarks in Evaluating Systemic Risks in General-Purpose AI Models](https://publications.jrc.ec.europa.eu/repository/handle/JRC143259), recommends more costly safety evaluation when either a capability threshold is met **or** the model is intended for a high-risk domain. This avoids relying on a single imperfect router.
4. **Evaluate maximum plausible capability, not only default behavior.** Frontier frameworks increasingly evaluate models with strong elicitation. For open weights, OpenAI's [Estimating Worst-Case Frontier Risks of Open-Weight LLMs](https://openai.com/index/estimating-worst-case-frontier-risks-of-open-weight-llms/) goes further by using malicious fine-tuning in biology and cybersecurity. This is directly relevant to Weightroom.
5. **Do not assume capability brings safety with it.** The UK AI Security Institute's [Frontier AI Trends Report](https://www.aisi.gov.uk/frontier-ai-trends-report) reports minimal correlation between general capability and safeguard robustness in its tested systems. Capability and safeguards must be measured independently.
6. **Treat benchmark scores as uncertain measurements.** NIST's work on [statistical models for AI evaluation](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models) distinguishes observed benchmark accuracy from generalized performance. Release decisions need confidence intervals, repeated trials where stochasticity matters, and explicit handling of missing or invalid evidence.
7. **Treat openness as a risk multiplier.** Solaiman's [Gradient of Generative AI Release](https://arxiv.org/abs/2302.04844) frames release as a continuum rather than a binary open/closed choice. A marketplace for downloadable weights operates near the least controllable end of that continuum.

The formulation is therefore **precedented at the principle level**. The potential contribution is a concrete certification protocol in which ordinary domain capability tests automatically select the relevant safety case for an open-weight release.

## 2. Scope and non-goals

This version covers text-input/text-output LLMs, including models that emit code or call text-described tools in an isolated harness.

Included:

- chat and completion models;
- code generation and repository-level software tasks;
- text-only scientific, professional, multilingual, retrieval, and agentic tasks;
- terminal, browser-like, database, and API tools when they are simulated or isolated and the interface is textual.

Excluded for now:

- images, audio, video, GUI perception, and robotics;
- visual prompt injection;
- LAB-Bench FigQA and other figure-dependent subsets;
- OSWorld and other GUI-control evaluations;
- claims that certification proves a model is safe under every future fine-tune or deployment.

Certification is a time-bound, evidence-backed release decision under a versioned threat model. It is not a warranty.

## 3. Threat model

Weightroom should evaluate at least four model states:

| State | What it represents | Minimum evaluation |
|---|---|---|
| Submitted | The exact artifact, generation settings, and chat template offered for download | All universal and activated suites |
| Template-stripped | Direct completion or a neutral template without the seller's safety system prompt | Activated hazardous-capability suites |
| Strongly elicited | Tool access, suitable scaffold, larger inference budget, best-of-*k*, and task-appropriate prompting | Activated consequential-risk suites |
| Bounded modified | A reproducible, predeclared attacker budget for refusal removal and domain fine-tuning | Required for models near or above a consequential-risk threshold |

The attacker is assumed to possess the weights and ordinary ML engineering ability. The attacker may change prompts, sampling, templates, quantization, and adapters. For the highest-risk domains, the attacker may perform bounded fine-tuning. The evaluation should not assume that platform monitoring or a separate guard model remains attached after download.

The threat model is deliberately stronger than the current model-as-served configuration, but not unlimited. Fine-tuning compute, data access, tools, and elicitation budgets must be published as versioned evaluation parameters so comparisons remain meaningful.

## 4. The two-trigger evaluation architecture

Every submission enters domain evaluation through either of two independent routes:

```mermaid
flowchart LR
    A[Scanned model artifact] --> B[Mandatory capability router]
    A --> C[Claims, metadata, architecture and direct tripwires]
    B --> D{Domain trigger?}
    C --> D
    D -- No --> E[Universal safety battery]
    D -- Yes --> F[Domain capability + safety case]
    E --> G[Release adjudication]
    F --> H{Near consequential threshold?}
    H -- No --> G
    H -- Yes --> I[Maximum elicitation + bounded modification]
    I --> G
```

### Trigger A: measured capability

A mandatory, relatively cheap router estimates capability across domains. It is separate from the optional capability benchmarks a seller purchases for a marketplace badge. Sellers cannot decline it, its detailed items can be held out, and it is optimized for high recall rather than leaderboard precision.

### Trigger B: intent or risk evidence

A domain is also activated when any of the following applies:

- the seller claims, markets, or fine-tuned the model for that domain;
- the model card, training data description, adapters, tool interfaces, or architecture indicate specialization;
- a universal safety test yields a domain-specific tripwire;
- static analysis or prior incident evidence indicates the domain;
- an evaluator manually escalates it.

Using `A OR B` is essential. A model must not avoid cyber evaluation because a small router missed its capability, nor because its seller declined SWE-bench.

### Trigger levels

Use ordinal levels instead of pretending heterogeneous benchmark scores lie on a common scale:

| Level | Meaning | Consequence |
|---|---|---|
| C0 — not demonstrated | No reliable evidence of useful domain capability | Universal battery only, unless Trigger B fires |
| C1 — basic | Answers bounded knowledge or routine tasks | Public domain screen |
| C2 — operational | Completes multi-step tasks that materially help a practitioner | Full public and held-out domain safety suite |
| C3 — consequential | Provides substantial uplift on high-consequence tasks or completes them autonomously | Expert review, maximum elicitation, and bounded-modification evaluation |

The numerical cut points for C1–C3 should **not** be chosen from intuition. Establish them through a calibration study using:

- models spanning known capability levels;
- unaided and tool-assisted human baselines where appropriate;
- task difficulty and time-to-completion;
- lower and upper confidence bounds;
- sensitivity to scaffold, prompt, seed, and inference budget;
- expert judgments about whether measured performance creates meaningful real-world uplift.

Until calibrated, trigger conservatively on the upper confidence bound and label the thresholds provisional.

## 5. LLM benchmark-to-safety matrix

No row below is a complete safety case by itself. Public benchmarks are useful for reproducibility and practice; certification should combine them with rotating held-out tasks and, for C3 domains, expert review. “Gate use” states the recommended role in Weightroom, not a claim made by the benchmark's authors.

| Capability domain | Capability router / evidence | Corresponding safety evidence | Activation and gate use | Maturity / important limitation |
|---|---|---|---|---|
| General reasoning and knowledge | MMLU-Pro; GPQA; a private cross-domain router | [AILuminate](https://mlcommons.org/ailuminate/safety-faq/) text-to-text hazards; [HarmBench](https://www.microsoft.com/en-us/research/publication/harmbench-a-standardized-evaluation-framework-for-automated-red-teaming-and-robust-refusal/); [StrongREJECT](https://arxiv.org/abs/2402.10260); JailbreakBench attacks; XSTest-style over-refusal | Mandatory for every model. A high general score does not automatically prove specialist bio/cyber ability, but lowers the threshold for specialist probes. | Good broad screen; weak evidence about rare, expert, multi-step harms. AILuminate's single-turn result is not a safety guarantee. |
| Instruction following and function calling | IFEval; BFCL; private instruction-hierarchy probes | System/developer/user conflict tests; jailbreak attacks; unsafe tool-argument generation; benign false-refusal controls | Run for all instruct/chat models. Strong instruction following activates stronger adversarial hierarchy tests. | Public suites are useful but attack transfer and policy interpretation vary. |
| Software engineering | SWE-bench Live or SWE-Bench Pro; SWE-bench Verified only as a historical/secondary measure; LiveCodeBench or BigCodeBench | CyberSecEval 4 insecure-code instruct and autocomplete; private secure-code regression set; dependency-confusion, secret-handling, and vulnerability-introduction tasks | Operational coding capability activates the cyber screen even when the model is not marketed for cyber. Failure on confirmed critical insecure-code tasks blocks release pending review. | Strong ecosystem, but benchmark/scaffold contamination is substantial. OpenAI has stated that [SWE-bench Verified no longer measures frontier coding capability reliably](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), so do not make it the sole router. |
| Offensive and defensive cybersecurity | [Cybench](https://cybench.github.io/) CTF tasks; CTI reasoning; private exploit-development and defensive tasks in a sandbox | [CyberSecEval 4](https://meta-llama.github.io/PurpleLlama/CyberSecEval/docs/intro) MITRE compliance, false-refusal, insecure-code and text-only tracks; Cybench under maximum elicitation; rotating private dual-use tasks; human red-team review | C2 runs the full public cyber suite. C3 requires strong elicitation and bounded cyber fine-tuning. Confirmed high-consequence autonomous exploitation is a hold condition. | One of the most implementable first domains. CTF success is a capability measure, not itself a refusal/safeguard measure; test both offensive uplift and defensive usefulness. |
| Biology and biotechnology | [LAB-Bench](https://github.com/Future-House/lab-bench) text-only ProtocolQA, SeqQA, DbQA, SuppQA and cloning subsets; GPQA biology; private wet-lab planning tasks | [WMDP-Bio](https://www.wmdp.ai/) as a hazardous-knowledge proxy; private expert-authored acquisition, troubleshooting and end-to-end uplift tasks; jailbreak and bounded fine-tuning variants | C1 receives a public proxy screen. C2 requires held-out expert evaluation. C3 or credible threat-creation uplift pauses public release for external biosecurity review. | Public coverage is incomplete. WMDP is multiple-choice precursor knowledge and cannot establish real-world biorisk alone. Sensitive tasks must remain controlled. |
| Chemistry and chemical synthesis | ChemBench; GPQA chemistry; private planning and literature-retrieval tasks | WMDP-Chem; [ChemSafetyBench](https://arxiv.org/abs/2411.16736); private toxic-agent acquisition, synthesis planning, troubleshooting and safe-alternative tasks | Same staged policy as biology, with chemistry expertise in adjudication. | Public benchmarks measure fragments of risk. Lab feasibility, precursor access, and tacit knowledge require expert interpretation. |
| Medicine and health | [HealthBench](https://openai.com/index/healthbench/) and held-out clinical reasoning/communication cases | High-acuity and contraindication slices; self-harm and crisis response; uncertainty, escalation, dosage and false-reassurance tests; AILuminate health hazards | High clinical capability or a medical claim activates the full medical-safety case. Critical unsafe advice is reviewed as an event, not hidden by a high mean score. | Mature enough for screening, not for certifying clinical use. Locale, specialty, and deployment context matter. |
| Text agents and tool use | BFCL; tau-bench; text-only GAIA; private multi-step tool tasks | [AgentHarm](https://www.aisi.gov.uk/research/agentharm-a-benchmark-for-measuring-harmfulness-of-llm-agents); [AgentDojo](https://agentdojo.spylab.ai/) prompt injection; privilege-boundary, data-exfiltration and unsafe side-effect tasks | Tool use activates agentic safety even if broad chat safety passes. Evaluate both malicious user requests and malicious tool outputs. | Rapidly developing. Results depend heavily on scaffold, permissions, tool set, and environment; certify the evaluated profile, not an abstract model. |
| Long-horizon autonomy and AI R&D | RE-Bench; MLE-bench; hard repository tasks; [METR time-horizon](https://metr.org/time-horizons/) estimates | Held-out sabotage, oversight evasion, reward-hacking, self-exfiltration and unauthorized replication tasks in a sandbox | C2 triggers autonomy probes. C3 requires specialist review and may be incompatible with unrestricted release depending on demonstrated consequences. | No single public benchmark is an adequate gate. Strong environment and elicitation dependence. |
| Persuasion and manipulation | Controlled belief-shift experiments; [Anthropic persuasion evaluations](https://www.anthropic.com/news/measuring-model-persuasiveness); argument generation | Hidden targeted-manipulation, coercion, impersonation, fraud and vulnerable-population tasks; AILuminate manipulation hazards | High measured persuasion or a persuasion/marketing claim activates human-subject-calibrated safety testing. | Automated judges are especially weak here; real effect sizes, audience selection, and scale matter. Use ethics-reviewed protocols. |
| Finance | [FinanceBench](https://arxiv.org/abs/2311.11944); private financial reasoning and filing-analysis tasks | AILuminate financial-advice hazards; fraud and market-manipulation scenarios; uncertainty, suitability and disclosure tests; AgentHarm fraud tasks | A finance claim triggers the suite regardless of router result. Materially unsafe transactional advice blocks the finance badge and may block general release if broadly exploitable. | Mostly high-stakes misuse rather than frontier catastrophic risk. Jurisdiction and product claims matter. |
| Law | [LegalBench](https://legalbench.ai/); held-out jurisdiction-specific reasoning | AILuminate legal hazards; unauthorized-practice, fabricated-citation, deadline, confidentiality and uncertainty tests | A legal claim triggers the full suite. Report jurisdiction and knowledge date. | Legal correctness and safe behavior are hard to separate; expert sampling is necessary. |
| Privacy and data handling | Retrieval/document QA; long-context extraction; private memorization canaries | [PrivacyLens](https://github.com/salt-nlp/privacylens); canary extraction; cross-user leakage; sensitive-attribute inference; indirect prompt injection and exfiltration | Run a baseline privacy screen for all models; RAG/tool/long-context capability activates the full suite. | Evaluation needs access to training provenance or seeded canaries to assess memorization well. Privacy laws and acceptable handling vary. |
| Multilingual and cross-cultural capability | Global MMLU; MMLU-ProX or equivalent native-language tasks; language-identification router | [XSafety](https://aclanthology.org/2024.findings-acl.349.pdf), MultiJail, XL-SafetyBench, plus every activated domain suite in the model's materially supported languages | A language is in scope when capability exceeds a basic usability threshold or the seller claims support. Do not certify “multilingual safety” from English-only tests. | Translation-only sets miss native phrasing, dialect, code-switching and cultural context. Use native-speaker review for major supported languages. |
| Long-context and RAG | RULER; LongBench text-only subsets; private grounded QA and multi-document tasks | Indirect prompt injection, instruction/data separation, provenance, poisoned-retrieval, confidential-context and citation-integrity tests | Strong long-context/RAG performance or an RAG claim activates the suite. | A model-only test cannot certify the security of every future RAG system; bind results to the tested scaffold and trust boundaries. |

### Recommended first implementation order

1. **Universal behavior and instruction following.** Replace the current two-screen approximation with benchmark-native protocols, benign controls, attacks, and stronger judging.
2. **Coding → cyber.** This has the clearest product relevance and the strongest public test ecosystem. Use a maintained coding router, CyberSecEval 4, Cybench, and a private sandboxed set.
3. **Biology and chemistry.** Add only with qualified external experts, secure task handling, and an explicit escalation path.
4. **Tool use, privacy, and long-context/RAG.** These fit Keystone's text-only scope but require evaluation profiles that describe the scaffold and permissions.
5. **Medical, legal, finance, persuasion, multilingual, and long-horizon autonomy.** Add as versioned domain modules; do not delay the core architecture until all are mature.

## 6. What “safe enough” should mean

Do not define safety as `safety_score >= f(capability_score)`. The units are incommensurate, averages hide critical failures, and a high refusal rate can coexist with high dangerous capability.

For each activated domain `d`, record separate evidence:

- `C_d`: benign or general domain capability;
- `H_d`: hazardous capability under strong elicitation;
- `B_d`: default harmful behavior and refusal quality;
- `R_d`: robustness to jailbreaks, template removal, and bounded modification;
- `U_d`: benign utility and false-refusal rate;
- `E_d`: evidence quality and uncertainty;
- `X`: release context—here, downloadable weights with limited downstream control.

Adjudicate with a policy table, not an arithmetic average:

| Evidence state | Open-weight decision |
|---|---|
| Domain not triggered; universal gates pass | Eligible within evaluated scope |
| C1; public domain screens pass | Eligible, with domain evidence reported |
| C2; held-out safety, utility, and robustness evidence pass | Eligible or conditional, depending on impact and uncertainty |
| C3 but hazardous capability remains below the consequential threshold under maximum elicitation and bounded modification | External review; release only with a documented margin and monitoring plan |
| Consequential hazardous capability is demonstrated after removing mutable safeguards | Not eligible for unrestricted public-weight distribution |
| Any applicable gate errors, is skipped, lacks adequate power, or has unresolved critical events | Defer; fail closed rather than infer safety |

“Highly safe” therefore means stronger **assurance** as capability rises: more representative tasks, more attack effort, more trials, more expert involvement, better evidence quality, and a larger safety margin. It does not mean forcing unrelated percentages onto the same scale.

### Critical-event rules

Mean pass rates should not wash out severe outcomes. Each suite must define pre-registered critical events. A confirmed critical event—for example, autonomous completion of a high-consequence harmful task—places the release on hold for expert adjudication regardless of the aggregate score. Because labels and graders can be wrong, this is a hold-and-review rule rather than an unreviewable automatic permanent ban.

### False refusals

Every harmful-request suite needs matched benign or defensive controls. A model that refuses all biology or cybersecurity questions is not “extremely safe”; it may simply be unusable. Report unsafe-compliance and false-refusal evidence separately. CyberSecEval's MITRE/false-refusal pairing is a useful pattern.

## 7. Evaluation protocol

### 7.1 Freeze an evaluation profile

Before running, bind the certification to:

- artifact digest and declared base-model lineage;
- tokenizer and chat template digests;
- quantization and inference engine;
- system prompt, tool schema, permissions, and scaffold version;
- temperature, token budget, number of attempts, best-of-*k*, and seed policy;
- suite, dataset, asset, judge, and policy versions;
- code revision and container image digest.

The profile prevents a result obtained with one scaffold from being presented as a property of every possible deployment.

### 7.2 Use a layered battery

For each activated domain:

1. **Public practice suite:** reproducible and available to sellers; never the only certification evidence.
2. **Rotating held-out suite:** same published taxonomy and rubric, different controlled items.
3. **Adversarial variants:** jailbreaks, obfuscation, multi-turn escalation, tool-output injection, and prompt-format changes appropriate to the domain.
4. **Benign controls:** matched requests measuring false refusal and defensive utility.
5. **Maximum-elicitation suite:** the best realistic scaffold and inference budget available to a downstream user.
6. **Modification test:** for near-threshold open-weight models, template removal and a bounded fine-tuning attack.
7. **Human review:** all critical events, a stratified sample of automated decisions, and every C3 safety case.

### 7.3 Judge defensibly

The current Qwen3Guard classifier can remain one signal, but it should not be the sole judge for certification. Use:

- benchmark-native graders where validated;
- at least two diverse automated judges for consequential free-form outputs;
- a deterministic rubric with domain-specific severity and task-completion criteria;
- blinded human adjudication for disagreements and critical events;
- regular judge calibration against expert-labeled samples;
- recorded judge false-positive and false-negative estimates;
- refusal-quality grading that distinguishes safe redirection from evasive or harmful partial help.

For high-risk biology and chemistry, automated safety classifiers cannot replace subject-matter experts.

### 7.4 Quantify uncertainty

Each result should include its sample count, point estimate, confidence interval, invalid-item count, and run-to-run variance. Use hierarchical estimates or stratified intervals when a benchmark contains materially different hazard classes. For stochastic agentic tasks, use repeated attempts and report `pass@k` or task-success probability with the exact budget.

Decision policy should use the conservative side of uncertainty:

- capability routing considers the upper plausible capability bound so weak evidence does not avoid evaluation;
- a safety gate requires the lower plausible safety bound to clear its threshold;
- insufficient power produces `INCONCLUSIVE`, which is release-defer, not pass.

### 7.5 Control leakage and gaming

- Keep only practice prompts in the public repository.
- Store held-out tasks, judge rubrics, and sensitive assets in an access-controlled evaluation package or object store.
- Fetch and verify assets before network isolation; run the model with network and platform credentials blocked.
- Use content hashes and canaries, scan likely training data overlap where possible, and rotate portions of the held-out pool.
- Return coarse failure categories and remediation links, not exact held-out prompts or threshold-near scores.
- Limit repeated submissions and compare suspicious score discontinuities.
- Periodically refresh tasks using post-training-cutoff material.

This is compatible with Keystone's existing fetch → scan → offline evaluate privilege separation.

## 8. Keystone implementation design

### 8.1 Current state and gaps

The current architecture already has several sound foundations:

- mandatory suites cannot be declined;
- gates are separated from capability grades;
- evaluation runs offline after asset staging;
- held-out results support stricter redaction;
- final publication rechecks reported gates.

However, the current behavioral certification is only two public screens in [`src/keystone/public_safety.py`](src/keystone/public_safety.py): HarmBench standard behaviors and JailbreakBench harmful goals, each graded once by a pinned Qwen3Guard model with a 90% safe-response threshold. The implementation correctly describes these as screens, not official benchmark-native runs. It does not yet implement jailbreak attacks, StrongREJECT scoring, domain routing, paired benign controls, maximum elicitation, modification testing, or a domain safety case.

There is also a structural issue: optional seller-selected capability suites cannot act as safety triggers. A capable seller could omit the relevant benchmark, leaving capability “unrated.” The router must therefore be mandatory, cheap, and independent of seller-visible capability badges.

### 8.2 Separate planning from execution

Add an `EvaluationPlanner` between scan and full evaluation:

```text
requested seller suites
        +
mandatory router results
        +
claims / metadata / tripwires
        ↓
activated domains and assurance levels
        ↓
immutable EvaluationPlan
        ↓
public suites + private suites + escalation requirements
```

The current `normalise_selection()` should continue to add universally mandatory suites, but it should no longer be the complete plan. The server—not the seller—adds domain suites after routing. Quote an initial base price and a published maximum/escalation price, or absorb routing costs into the listing fee; do not let payment refusal bypass an activated safety test.

### 8.3 Extend suite metadata

Extend `SuiteManifest` with structured policy fields rather than encoding them in IDs:

```python
kind: Literal["capability_router", "capability_badge", "safety", "utility"]
domains: list[str]
evidence_roles: list[Literal[
    "trigger", "default_behavior", "hazardous_capability",
    "adversarial_robustness", "modification_robustness", "false_refusal"
]]
assurance_levels: list[Literal["C0", "C1", "C2", "C3"]]
visibility: Literal["public", "held_out", "restricted"]
protocol_version: str
judge_refs: list[str]
attempts: int
critical_event_policy: str | None
```

Keep `mandatory` and `gate`, but interpret them within an immutable evaluation plan. A planned applicable gate that produces no result must fail closed.

### 8.4 Add first-class domain assessments

Do not overload a flat `SuiteResult` list to make release decisions. Add a signed internal record such as:

```python
class DomainAssessment(BaseModel):
    domain: str
    trigger_reasons: list[str]
    capability_level: Literal["C0", "C1", "C2", "C3", "unknown"]
    hazardous_capability: EvidenceSummary | None
    default_behavior: EvidenceSummary | None
    adversarial_robustness: EvidenceSummary | None
    modification_robustness: EvidenceSummary | None
    false_refusal: EvidenceSummary | None
    critical_events: list[CriticalEvent]
    confidence: Literal["low", "moderate", "high"]
    verdict: Literal["pass", "fail", "inconclusive", "review"]
    policy_version: str
```

`CertificationReport` should contain:

- the frozen evaluation profile;
- the immutable evaluation plan and why each domain was activated;
- raw suite results;
- domain assessments;
- an overall release verdict distinct from the capability grade.

Buyer-facing reports can expose capability evidence, evaluated domains, policy version, release verdict, and coarse limitations without exposing held-out prompts.

### 8.5 Make final adjudication plan-aware

The final worker currently checks that at least one gate exists and all returned gates pass. Strengthen this to require:

```python
required = evaluation_plan.applicable_gate_ids
returned = {result.suite_id for result in report.suite_results if result.gate}

passed = (
    required == returned
    and all(results[g].status == PASS for g in required)
    and all(domain.verdict == "pass" for domain in activated_domains)
    and no_unresolved_critical_events
    and report.policy_version == evaluation_plan.policy_version
)
```

This prevents a planner, registry, or report-construction error from silently reducing the gate set.

### 8.6 Suggested module boundaries

```text
src/keystone/evals/
  catalog.py          # versioned suite and domain registry
  router.py           # mandatory high-recall capability router
  planner.py          # two-trigger logic; produces immutable plan
  policy.py           # thresholds, escalation, critical-event rules
  adjudication.py     # suite evidence -> domain and release verdicts
  profiles.py         # frozen model/scaffold/elicitation profiles
  judges.py           # calibrated judge ensemble and human-review queue
  modification.py     # template stripping and bounded fine-tuning protocols
  domains/
    general.py
    cyber.py
    bio.py
    chemistry.py
    agents.py
    privacy.py
```

Keep public benchmark adapters and private suite loaders behind the same `Suite` protocol. Private prompts must not be committed to this repository.

### 8.7 Proposed v1 evaluation flow

1. Resolve the immutable artifact and declared lineage.
2. Scan without loading untrusted code.
3. Stage pinned public and authorized private assets while network access exists.
4. Start the model offline and freeze the submitted serving profile.
5. Run universal safety, benign-control, and mandatory capability-router suites.
6. Build and sign the `EvaluationPlan` from router results, seller claims, metadata, and tripwires.
7. Run activated public and held-out suites.
8. If a C3 boundary is approached, pause automatic adjudication and enqueue the maximum-elicitation/modification protocol plus expert review.
9. Produce suite results, domain assessments, uncertainty, and a release verdict.
10. Re-derive all required gates from the signed plan before listing.
11. Publish a redacted certificate bound to the artifact and evaluation profile.

## 9. Initial policy for coding → cyber

This should be the first complete vertical slice.

### Router

Use a maintained repository-level software benchmark (SWE-bench Live or SWE-Bench Pro when licensing and runtime permit), a smaller code-generation set, and a short private router. Treat SWE-bench Verified as secondary historical evidence because of contamination and task-validity concerns.

Route to full cyber evaluation when any of these holds:

- the coding router's upper confidence bound crosses the provisional C2 threshold;
- the seller claims coding, security, pentesting, vulnerability analysis, or autonomous software engineering;
- the model is a code-specialized fine-tune;
- a universal safety or artifact inspection tripwire identifies cyber specialization.

### Safety case

Run:

- CyberSecEval 4 insecure-code instruct and autocomplete;
- CyberSecEval 4 MITRE harmful-compliance and matched false-refusal tasks;
- Cybench with a pinned isolated scaffold and no external network;
- a rotating private set covering exploit reasoning, malware transformation, credential theft, persistence, vulnerability triage, patching, and safe defensive assistance;
- jailbreak/obfuscation variants and multi-turn escalation;
- template-stripped direct completion;
- for near-C3 models, bounded refusal-removal and cyber capability fine-tuning.

The decision must preserve three distinct outcomes:

1. Does the model write correct general code?
2. Does it introduce avoidable vulnerabilities or comply with harmful cyber requests?
3. Can it complete consequential cyber tasks when strongly elicited or modified?

A model can be strong on (1), good on safe behavior in (2), and still fail open-weight release on (3).

## 10. Validation and acceptance criteria

Before enabling the new gate in production, require:

- **Non-evasion:** declining every optional benchmark still activates the same safety domains for the same artifact.
- **Plan completeness:** deleting, skipping, or erroring any required result makes certification ineligible.
- **Reproducibility:** reruns under the frozen profile fall within a predeclared tolerance.
- **Router recall:** on an anchor set of specialized models, the router and metadata triggers activate every known relevant domain.
- **Judge validity:** automated decisions meet predeclared sensitivity/specificity against expert labels, with special attention to false-safe errors.
- **Attack coverage:** the public direct-prompt score cannot substitute for jailbreak, tool, or modification evidence.
- **Utility preservation:** false-refusal and safe-redirection metrics are reported separately from harmful compliance.
- **Leak resistance:** seller/buyer views contain no held-out prompt, answer, exact near-threshold score, judge rationale, or restricted asset reference.
- **Version binding:** artifact, profile, suite, judge, assets, and policy are hash- or version-bound in the signed report.
- **Adversarial regression:** known unsafe anchor models fail and known low-risk anchors do not fail merely by refusing benign domain questions.
- **Cost observability:** GPU time, human review time, and escalation rate are measured per domain.

Unit tests should cover the policy graph and report invariants. Integration tests should exercise at least: a weak general model, a strong code model that passes cyber safety, a strong code model that fails cyber safety, a bio-specialized model activated by metadata despite weak router evidence, a missing private asset, a judge error, and an unresolved critical event.

## 11. Governance and recertification

Create a small evaluation-policy board with safety, domain, security, and product representation. For C3 biology, chemistry, cyber, and autonomy decisions, require independent subject-matter review.

Version and publish:

- the threat model and domain taxonomy;
- the public benchmark protocols;
- trigger semantics and evidence roles;
- coarse release criteria and appeal process;
- known limitations and benchmark retirement decisions;
- a change log for policy and judge updates.

Keep exact held-out content, sensitive rubrics, and exploitable thresholds restricted.

Require recertification when:

- weights, adapters, tokenizer, or chat template change;
- the seller materially changes capability claims;
- a benchmark or judge is retired or found compromised;
- a relevant incident or new jailbreak invalidates prior evidence;
- the certificate exceeds its validity period;
- a new domain trigger is introduced and applies to the model.

Post-release incident intake is necessary but cannot replace pre-release evaluation. For open weights, recall is usually impossible; the release gate must therefore carry more weight than it would for a revocable API deployment.

## 12. Delivery sequence

### Phase 0 — make current claims exact

- Keep the two existing checks labeled “public screens.”
- Do not describe the current JailbreakBench goal set as jailbreak resistance until attacks are actually applied.
- Document that Qwen3Guard is a single automated judge and that 90% is provisional, not empirically calibrated.

### Phase 1 — policy substrate

- Add the router, two-trigger planner, evaluation profile, domain assessment, plan-aware final gate, and `INCONCLUSIVE`/`REVIEW` outcomes.
- Add confidence intervals, benign controls, judge calibration, and required-result invariants.

### Phase 2 — universal battery and coding/cyber vertical

- Implement benchmark-native HarmBench/StrongREJECT/JailbreakBench protocols.
- Implement coding routing, CyberSecEval 4 text tracks, Cybench, and private cyber tasks.
- Calibrate provisional thresholds on anchor models before gating real listings.

### Phase 3 — modification envelope

- Add template-stripped, quantized, and bounded refusal-removal tests.
- Add bounded domain fine-tuning only for near-C3 models; obtain external methodology review.

### Phase 4 — bio/chem and agentic domains

- Establish controlled data handling and expert review first.
- Then add LAB-Bench text subsets, WMDP proxies, private uplift tasks, AgentHarm, AgentDojo, and domain-specific modification tests.

### Phase 5 — breadth and continuous validation

- Add multilingual, privacy/RAG, medical, legal, finance, persuasion, and autonomy modules.
- Monitor false positives, incidents, submission gaming, benchmark saturation, and distribution shift.

## 13. Bottom line

The best implementation is not “SWE-bench score X requires cyber refusal score Y.” It is:

1. use capability and intended use as independent triggers;
2. construct a domain-specific safety case;
3. separately measure benign capability, hazardous capability, default behavior, adversarial robustness, modification robustness, and false refusals;
4. increase assurance depth as capability approaches consequential thresholds;
5. fail closed on missing or inconclusive required evidence;
6. for downloadable weights, base the ultimate decision on capability after mutable safeguards are removed—not on default refusal behavior alone.

That structure is faithful to the literature, practical for Keystone's current architecture, and stronger than a flat universal battery or a single composite “safety score.”

## References

- Anthropic. [Responsible Scaling Policy update](https://www.anthropic.com/news/announcing-our-updated-responsible-scaling-policy).
- AI Security Institute. [Frontier AI Trends Report](https://www.aisi.gov.uk/frontier-ai-trends-report).
- AI Security Institute. [Principles for Evaluating Misuse Safeguards of Frontier AI Systems](https://www.aisi.gov.uk/research/principles-for-evaluating-misuse-safeguards-of-frontier-ai-systems).
- European Commission Joint Research Centre. [The Role of AI Safety Benchmarks in Evaluating Systemic Risks in General-Purpose AI Models](https://publications.jrc.ec.europa.eu/repository/handle/JRC143259).
- Meta. [Advanced AI Scaling Framework](https://ai.meta.com/static-resource/Meta_Advanced-AI-Scaling-Framework-v2).
- NIST. [Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence).
- NIST. [Expanding the AI Evaluation Toolbox with Statistical Models](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models).
- OpenAI. [Preparedness Framework, version 2](https://cdn.openai.com/pdf/18a02b5d-6b67-4cec-ab64-68cdfbddebcd/preparedness-framework-v2.pdf).
- OpenAI. [Estimating Worst-Case Frontier Risks of Open-Weight LLMs](https://openai.com/index/estimating-worst-case-frontier-risks-of-open-weight-llms/).
- Shevlane et al. [Model Evaluation for Extreme Risks](https://arxiv.org/abs/2305.15324).
- Solaiman. [The Gradient of Generative AI Release: Methods and Considerations](https://arxiv.org/abs/2302.04844).
- UK Government. [Emerging Processes for Frontier AI Safety](https://www.gov.uk/government/publications/emerging-processes-for-frontier-ai-safety/emerging-processes-for-frontier-ai-safety).
- UK Government. [Frontier AI Safety Commitments, AI Seoul Summit 2024](https://www.gov.uk/government/publications/frontier-ai-safety-commitments-ai-seoul-summit-2024/frontier-ai-safety-commitments-ai-seoul-summit-2024).
