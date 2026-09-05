# Design Doc: Sovereign Model Exchange (working title: **Keystone**)

> A marketplace for downloadable open-weight models, built on an independent trust-and-rating layer that lets enterprises and governments adopt open models without training their own.

|  |  |
| :---- | :---- |
| **Status** | Draft v0.1 — concept exploration |
| **Author** | — |
| **Last updated** | September 2026 |
| **Reviewers** | — |
| **One-liner** | Civitai's creator flywheel \+ a Moody's-style rating agency for open models, aimed at the sovereign-AI moment. |

---

## 1\. TL;DR

Open-weight models are proliferating, but there is no trusted, enterprise-grade place to **buy a downloadable fine-tuned model and run it yourself**. Existing model hubs (Hugging Face) are discovery layers, not curated paid marketplaces; the one real creator economy (Civitai) is image-gen-only. Meanwhile the whole discovery layer is consolidating — Nvidia signed a definitive agreement to acquire Hugging Face (\~$12.9B, Sept 2 2026, expected to close H1 2027\) — and the industry narrative has swung hard toward **sovereignty**: organizations and nations wanting to own, audit, and run their own AI rather than renting it from US hyperscalers.

**Keystone** is a two-sided marketplace with a twist: the product isn't really the catalog, it's the **trust spine** — an independent certification/rating layer (provenance, license-cleanliness, security scan, capability \+ safety eval) that we put our name on. Creators upload fine-tuned open models; buyers download and run them locally. A tiered offering serves a cheap hobbyist tier (weights only) and a high-value sovereign/enterprise tier (weights \+ training dataset \+ fine-tuning recipe \+ provenance dossier). Trust first, transactions second.

**The single biggest open decision:** which flywheel do we light first — the fast, liquid, piracy-prone hobbyist market, or the slow, high-touch, defensible enterprise/sovereign market? They have opposite physics and we cannot serve both well on day one.

---

## 2\. Problem & thesis

### 2.1 The gap

If you fine-tune an open model on a specialized dataset today, you have no good commercial home for it as a **downloadable artifact**:

- **Hugging Face** hosts weights but is a hub, not a paid, curated, quality-gated marketplace. (And it's about to sit inside Nvidia.)  
- **Civitai** has real creator monetization — paid downloads, patron-gated access, an internal currency — but is culturally and technically centered on Stable Diffusion / LoRAs.  
- **LLM-weight marketplaces** (FineTuneHQ, aimodelplace, assorted crypto "model marketplaces") are thin, unmaintained, or actually inference-compute marketplaces wearing a marketplace label.  
- **Inference marketplaces** (OpenRouter-style, plus the decentralized crop — Morpheus, etc.) solve *renting tokens*, not *owning artifacts*. They cannot serve an air-gapped or data-sovereign buyer at all.

Nobody credibly owns: **model discovery → verification → licensing → download → provenance/trust**, for buyers who need to run the model themselves.

### 2.2 Thesis

The macro tailwind ("buy, don't train") is real and being validated with real money (Nvidia's move, Jensen's explicit "open models enable sovereignty" framing). But the naive version — "Gumroad for weights" — is structurally weak (see §11 and §17). The defensible version wraps the marketplace around a **trust layer** that is (a) hard to build, (b) valuable independently of transaction volume, and (c) *more* valuable precisely because the dominant discovery layer is being absorbed by the dominant GPU vendor. A neutral, vendor-independent trust oracle is a feature, not a footnote.

---

## 3\. Market context — why now

1. **Consolidation of the discovery layer.** Nvidia acquiring Hugging Face concentrates the neutral commons under a hardware vendor. Regulated and sovereign buyers may not want their provenance/safety oracle owned by the company selling the GPUs. **Neutrality becomes a wedge.**  
2. **Sovereign-AI narrative is funded and political.** Governments, defense, banks, and healthcare want on-prem / air-gapped / auditable AI. "Buy don't train" only works if there's a trustworthy place to buy from.  
3. **Supply-chain fear is now board-level.** A recent high-profile Hugging Face security breach, plus a steady drip of research on malicious/Trojan model weights and poisoned pickles, has made "is this open model safe to load?" a real procurement blocker.  
4. **Open-weight quality has crossed the "good enough" line** for many enterprise tasks, so the addressable set of buyers who *could* deploy an open model (rather than a frontier API) is now large.

---

## 4\. Non-goals (scope guardrails)

To avoid boiling the ocean, Keystone is explicitly **not**:

- ❌ An inference / GPU-rental marketplace. That's a different business with different physics; the decentralized-compute crowd already lives there.  
- ❌ A "decentralized Hugging Face" for its own sake. Decentralized storage/distribution is an implementation detail we may adopt later (torrent/P2P/IPFS-style), not the pitch.  
- ❌ A from-scratch security scanner. Model-scanning is a crowded, funded category (HiddenLayer, Wiz, Checkmarx, ProtectAI, plus OSS like modelscan and garak). We integrate the commodity layer and put our brand on the *rating*, not the scan.  
- ❌ A frontier-model lab. We don't train base models; we sit on top of the open ecosystem.

---

## 5\. Product overview

Two customer-facing surfaces sharing one trust spine.

                       ┌─────────────────────────────┐

                       │        TRUST SPINE          │

                       │  provenance • license clear │

                       │  security scan • eval/rating│

                       └──────────────┬──────────────┘

                                      │ certifies every listing

        ┌─────────────────────────────┴─────────────────────────────┐

        │                                                             │

┌───────▼────────┐                                          ┌─────────▼─────────┐

│  HOBBYIST TIER │                                          │ SOVEREIGN TIER    │

│  weights only  │                                          │ weights \+ dataset │

│  low price     │                                          │ \+ recipe \+ dossier│

│  fast checkout │                                          │ high-touch, high $ │

└────────────────┘                                          └───────────────────┘

### 5.1 The unit of value (important)

Naively, the product is "a frozen `.safetensors` file." That asset **depreciates fast** as base models improve. So Keystone treats a listing as a **bundle**, and the durable, re-usable parts are what carry premium value:

| Component | What it is | Durability | Tier |
| :---- | :---- | :---- | :---- |
| **Weights** | The fine-tuned checkpoint (safetensors/GGUF) | Low — rots each base cycle | Both |
| **Eval card** | Independent benchmark \+ safety results | Medium | Both |
| **Provenance dossier** | Lineage, base model, country of origin, training-data attestations, license chain | High | Sovereign |
| **Training dataset** | The data the fine-tune was built on | High — re-bakeable | Sovereign (paid add-on) |
| **Recipe** | Reproducible fine-tuning config/script | High — travels across base generations | Sovereign (paid add-on) |

> **Key insight:** in a fast-moving field, the durable asset is the *dataset \+ eval \+ recipe*, not the frozen weights. The sovereign tier monetizes durability; the hobbyist tier monetizes convenience.

---

## 6\. The trust & rating layer (the actual moat)

This is where the company's defensibility lives. Anyone can bolt on an off-the-shelf scanner; the brand is the **independent rating we sign our name to**.

### 6.1 What we certify (four distinct products under one badge)

1. **Security** — no malicious serialization, no known-vulnerable dependencies, weight-integrity attestation / signing. *(Mostly borrowed tooling.)*  
2. **Provenance & lineage** — verified base model, derivation chain, country of origin, training-data attestations, AI-SBOM. *(Partly borrowed, partly our schema.)*  
3. **License cleanliness** — the derivation is legally distributable under stated terms (see §14). *(Our IP \+ legal review.)*  
4. **Capability & behavioral eval** — does it do what it claims, and does it avoid toxic/illegal/unsafe output? *(Our held-out, rotating evals — the hardest and most defensible piece.)*

### 6.2 Why the eval piece is the moat

- Public benchmarks get **Goodharted** — sellers overfit to them. Our defense is **private, rotating, held-out eval sets \+ red-teaming** (garak-style behavioral probing plus our own suites). This is expensive and hard to replicate — and that expense *is* the defensibility.  
- Continuous re-evaluation as (a) models drift, (b) new attacks emerge, (c) base models advance and old listings get re-rated "outdated."

### 6.3 Liability posture (do this carefully)

We **rate and measure; we do not warrant.** Rating agencies get sued. Contract language, methodology transparency, and "as-tested, at-time-of-test" framing are load-bearing. Legal review is a v1 requirement, not a v2 nicety.

---

## 7\. Marketplace mechanics

- **Model pages:** creator profile, versioning, eval card, provenance dossier, license, price, download analytics, reviews.  
- **Creator tools:** listing wizard, automated pre-submission scan/eval, revenue dashboard, re-bake reminders when a new base model lands.  
- **Buyer flow:** search/filter (by task, base model, license, rating, country of origin) → verify against dossier → purchase → download → optional dataset/recipe add-on.  
- **Reputation:** creator track record, verified eval history, buyer reviews; "outdated but functional" is a state, not a delisting.  
- **Trust badges:** "Keystone Certified" tiers gate what enterprise buyers will even look at.  
- **Distribution:** start with plain hosted download \+ signed artifacts; P2P/decentralized distribution is a later optimization, not a v1 requirement.

---

## 8\. Customer segments (they have opposite standards — do not blur them)

|  | Hobbyist / SMB dev | Enterprise | Sovereign / gov / defense |
| :---- | :---- | :---- | :---- |
| Buys | Weights | Weights \+ dossier | Full bundle \+ dataset \+ recipe |
| Cares about | "Good enough for my thing," price, speed | Compliance, license risk, security | Auditability, ownership, country-of-origin, air-gap |
| Capability bar | Low (better than base-in-an-afternoon is optional) | High | High \+ must be inspectable |
| Sales motion | Self-serve, fast | Mid-touch | Slow, high-touch, procurement |
| Piracy exposure | High | Low (contractual) | Low |
| Price point | $ | $$$ | $$$$ |

⚠️ **Tension to resolve:** "capability doesn't matter, it depends on consumer need" is true for the hobbyist and false for the sovereign buyer. The pitch quietly merges two customers with opposite quality standards. Pick a lead segment (§16).

---

## 9\. Creator economics — the flywheel question

The marketplace lives or dies on **creator ROI per model vs. cost to produce it.**

- Civitai works because image LoRAs are *cheap* (an afternoon, a few dollars of compute), so creators happily re-bake every base-model cycle (SD1.5 → SDXL → Flux). Old inventory going stale doesn't kill the platform because new inventory is cheap to make.  
- A quality **LLM** fine-tune \+ eval costs more and rots faster, so the creator's payback window is tighter. The flywheel only spins if **payout-per-model \> cost-to-redo-per-base-cycle.**

**This is the core empirical unit-economics question and should be validated before heavy build.** Levers to widen creator payback:

- Recurring revenue (dataset/recipe subscriptions, re-bake-on-new-base as a service) instead of one-time file sales.  
- Bundling the durable assets (dataset/recipe) that don't rot.  
- Lowering creator cost (managed fine-tuning \+ auto-eval pipeline on-platform).

---

## 10\. Revenue model

- **Take rate** on marketplace transactions (Civitai/Gumroad-style).  
- **Certification fees** — creators pay to get listed/verified; buyers pay for the rating subscription / continuous re-eval.  
- **Sovereign tier premium** — high-margin bundles (weights \+ dataset \+ recipe \+ dossier), likely sold high-touch.  
- **Enterprise trust subscription** — "run any open model past us before you deploy it," even models not bought on Keystone. *(This is the wedge that doesn't depend on marketplace liquidity.)*  
- Later: managed re-baking, hosted eval-as-a-service, white-label trust layer.

---

## 11\. Structural risks the model must survive

These are the reasons the gap is still open. Each has a mitigation, none is fully solved.

1. **Frozen fine-tunes depreciate.** → Mitigate with the creator flywheel (new inventory) \+ durable bundle assets (dataset/recipe). Depreciation makes *old listings* useless, not the *platform* — as long as the flywheel spins.  
2. **Best sellers prefer inference (revenue capture).** → The sovereign framing answers this: buyers need *artifacts* to run locally/air-gapped, which no API marketplace can serve. Sellers aren't cannibalizing an API; they're reaching a segment that only wants weights.  
3. **Piracy / re-sharing (the leaky bucket).** Downloadable weights are trivially re-sharable. One buyer can leak the file and creator revenue evaporates. → Shift value to the **ongoing relationship** (updates, re-bakes, support, dataset access) rather than the one-time file. Watermarking helps attribution but is removable via fine-tuning; treat it as forensic, not preventive.  
4. **Sovereign buyers distrust third-party artifacts by nature.** A black-box stranger's fine-tune is the opposite of sovereign. → The **dataset \+ recipe \+ provenance** tier turns a black box into an auditable, re-bakeable asset. (This is the strongest single design decision in the doc.)  
5. **Dataset \= the creator's real moat.** Best creators sell weights but guard the crown-jewel dataset, so the highest-value tier has the **thinnest supply.** → Pricing, revenue share, and exclusivity terms to coax datasets out; expect selection effects.  
6. **Two opposite flywheels.** Hobbyist (fast, liquid, piracy-prone) vs. sovereign (slow, defensible). Serving both on day one splits focus. → Pick one (§16).  
7. **Certification liability.** → Rate, don't warrant (§6.3).  
8. **Incumbent security vendors.** → Don't compete on scanning; compete on the *independent rating brand* \+ license clearance \+ the curated index.

---

## 12\. Licensing & legal (load-bearing, not "stuff we don't care about")

Open ≠ freely redistributable. This directly constrains what can be listed and how it's priced:

- **Apache-2.0 base:** derivative weights can be distributed under any license, including proprietary. ✅ Cleanest.  
- **Llama community license:** you must keep Meta's license and notices; you **cannot** relicense a Llama fine-tune under Apache-2.0 even with substantial proprietary data. Redistribution terms and acceptable-use clauses travel with it.  
- **RAIL / OpenRAIL:** use-restriction clauses must be carried through to the buyer's contract.

**Implication:** the license-cleanliness certification (§6.1) is not paperwork — it's a genuine buyer pain and a real part of the moat. License chain must be a first-class field on every listing and enforced at upload.

---

## 13\. Technical architecture (high level, v1)

Creator upload ─▶ Ingest & validate ─▶ Automated pipeline ─▶ Human/legal review ─▶ Listing

                                          │

                                          ├─ security scan (borrowed: modelscan-class, dependency checks)

                                          ├─ provenance extraction \+ AI-SBOM \+ signing/attestation

                                          ├─ license classifier \+ chain check

                                          └─ eval harness (borrowed red-team \+ PROPRIETARY held-out suites)

Buyer ─▶ Catalog/search ─▶ Purchase ─▶ Entitlement ─▶ Signed download (+ optional dataset/recipe)

                                                          │

                                                          └─ integrity verify against signed manifest

- **Storage/distribution:** hosted object store \+ signed manifests in v1. Optional P2P/decentralized distribution as a later cost/latency optimization — deliberately deferred.  
- **Eval infra:** the crown jewels. Private, versioned, rotating eval sets; sandboxed execution; continuous re-scoring as base models advance.  
- **Payments:** standard rails first; crypto/USDC optional later for the decentralized/cross-border narrative — not required for v1.

---

## 14\. Safety & security stack — borrow vs. build

| Capability | Borrow | Build |
| :---- | :---- | :---- |
| Malicious-weight / pickle scanning | ✅ (modelscan-class, vendor APIs) | — |
| Dependency / CVE checks | ✅ | — |
| Weight signing / attestation | ✅ (sigstore-style) | integration \+ policy |
| Provenance schema / AI-SBOM | partly | our normalized dossier format |
| License classification | tooling assists | our rules \+ legal review |
| Capability \+ behavioral eval | red-team frameworks (garak-class) | **proprietary held-out suites, rotation, red-team** |
| The *rating* itself | — | **100% ours — this is the brand** |

Principle: **the more borrowable a capability is, the less it makes us credible.** Spend build effort on the rating and the held-out evals.

---

## 15\. Go-to-market & sequencing

**Recommended: lead with the trust wedge, then a single flywheel.**

- **Phase 0 — Trust wedge (no marketplace liquidity needed):** sell "run any open model past Keystone before you deploy" to a narrow, acute segment. This builds the brand and the eval infra while dodging the two-sided cold-start problem entirely.  
- **Phase 1 — Pick ONE flywheel:**  
  - *Option A — Own a rabid niche first (the Civitai playbook):* a specific vertical/task where creators are motivated and buyers are underserved. Fast liquidity, learn the mechanics, tolerate piracy.  
  - *Option B — Sovereign/enterprise-first:* high-touch, high-value bundles; slower, but defensible and aligned with the macro moment and the neutrality wedge.  
- **Phase 2 — Layer the second surface** on top of the trust brand once one side is liquid.

>   
> Civitai won by owning a rabid niche *first*, not by serving "every company in the world." Breadth is the reward for winning a wedge, not the entry strategy.

---

## 16\. Success metrics

- **Trust wedge:** \# of enterprises running models through certification; certification revenue independent of marketplace GMV.  
- **Supply health:** new quality listings / base-model cycle; **creator payback ratio** (payout-per-model ÷ cost-to-produce) — the flywheel's vital sign.  
- **Demand health:** GMV, repeat buyers, sovereign-tier attach rate (dataset/recipe).  
- **Trust integrity:** eval Goodhart rate (how often "certified" models fail in the wild), piracy leakage rate.

---

## 17\. Open questions / decisions needed

1. **Which flywheel first — hobbyist niche or sovereign/enterprise?** (Highest-order decision; everything downstream depends on it.)  
2. **Is creator payback positive for LLM fine-tunes** the way it is for image LoRAs? Needs a real unit-economics model \+ creator interviews before heavy build.  
3. **Lead segment's sharpest pain** — regulated enterprise (compliance) vs. sovereign/gov (geopolitical/ownership)? They want overlapping but different things.  
4. **Do we sell the trust layer standalone** (certify models not bought here) from day one? (I lean yes — it's the liquidity-independent wedge.)  
5. **How do we coax datasets out of creators** when the dataset is their real moat?  
6. **Piracy stance** — how much do we invest in prevention vs. accepting it and monetizing the relationship?  
7. **Where does neutrality live** relative to a post-acquisition Hugging Face — partner, complement, or explicit alternative?

---

## 18\. Appendix

### Glossary

- **Weights / checkpoint:** the trained parameters you download and run (safetensors, GGUF).  
- **Recipe:** the reproducible fine-tuning config/script; re-usable across base-model generations.  
- **Provenance dossier:** verified lineage — base model, derivation chain, country of origin, training-data and license attestations.  
- **AI-SBOM:** software-bill-of-materials analog listing a model's components for audit.  
- **Held-out eval:** private benchmark not shared with creators, to resist overfitting/Goodharting.  
- **Weight attestation / signing:** cryptographic proof a downloaded artifact matches the certified one.

### Reference landscape (as of Sept 2026\)

- **Nvidia–Hugging Face:** definitive agreement, \~$12.9B, signed Sept 2 2026, expected close H1 2027; commitment to keep the platform open.  
- **Creator-economy analogue:** Civitai (image-gen, paid downloads, patron gating, internal currency).  
- **Thin LLM-weight marketplaces:** FineTuneHQ, aimodelplace, assorted crypto "model marketplaces."  
- **Inference/compute (adjacent, not us):** OpenRouter-style routers; decentralized compute (Morpheus, et al.).  
- **Security-scanning incumbents (borrow, don't rebuild):** HiddenLayer, Wiz, Checkmarx, ProtectAI; OSS modelscan, garak.  
- **Governance frameworks to align with:** NIST AI RMF, ISO 42001\.

---

*This is a v0.1 concept doc. The riskiest assumptions (creator payback economics, lead-segment choice, piracy tolerance) should be pressure-tested with real numbers and customer conversations before committing to a build.*  
