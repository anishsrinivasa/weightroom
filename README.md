# Keystone

Certification pipeline for open-weight models. Submit a model, get back a
reproducible report: what it is, where it came from, whether it is safe to
load, and how it behaves.

Concept and strategy live in [model-marketplace-design-doc.md](model-marketplace-design-doc.md).
This repo is the platform that implements it.

**Scope right now: text-only LLMs.** VLM support is an additive layer, not a
rewrite — see [Seams](#seams-kept-open-for-vlm).

---

## The pipeline

```
fetch  ──▶  scan  ──▶  evaluate  ──▶  report
```

Three stages, three privilege levels. The split *is* the sandbox story:

| Stage | Network | Compute | Untrusted code runs? |
| :--- | :--- | :--- | :--- |
| `fetch` | **on** | CPU | no — bytes only |
| `scan` | **off** | CPU | no — inspects, never loads |
| `evaluate` | **off** | GPU | **yes** — weights load here |

`evaluate` is the dangerous stage and therefore the locked-down one
(`block_network=True`, `restrict_modal_access=True`). Cutting its egress does
two jobs at once: it contains a malicious checkpoint, and it stops held-out
eval prompts from ever leaving the box. Those prompts are the moat; if they
leak, the rating is worthless.

### Why models get served

Certification is a one-time job, but you cannot behaviourally evaluate a model
without running inference on it. So `evaluate` stands up an ephemeral vLLM
server on loopback inside the sandbox, points the suites at it, and tears it
down when the job ends. **Nothing is ever served to a buyer** — Keystone sells
downloadable artifacts, not inference.

A server rather than vLLM's offline batch API, because: continuous batching
happens no matter how the harness writes its loop, multi-turn red-team probes
work naturally, standard eval frameworks speak the protocol unmodified, and
suites can be developed against *any* OpenAI-compatible endpoint with no Modal
account. If the suites turn out uniformly single-turn and batch-shaped, add an
`OfflineBatchClient` behind `ModelClient` — no suite changes.

---

## Setup

```bash
uv venv --python 3.12          # 3.13+ has no torch/vLLM wheels yet
uv pip install -e .
python -m pytest -q            # 27 offline tests, no GPU needed
```

Modal (for anything that actually runs a model):

```bash
modal setup
modal secret create huggingface HF_TOKEN=hf_...
```

## Use

```bash
keystone suites                                    # what's discoverable
keystone certify Qwen/Qwen2.5-0.5B-Instruct        # full run
keystone certify <ref> --suite stub_safety         # one suite
keystone schema                                    # regenerate the JSON Schema
```

Reports land in `out/<report_id>.json`.

---

## The seam

The platform owns *how* things are measured. The harness owns *what* is
measured. They meet at two files and nowhere else:

- **[`schema.py`](src/keystone/schema.py)** — the report model. Authoritative;
  `schemas/report.schema.json` is generated from it, and a test fails if they
  drift.
- **[`suites.py`](src/keystone/suites.py)** — the suite contract.

A suite gets a `SuiteContext` and nothing else:

```python
ctx.client        # ModelClient — .chat() / .complete(), safe to call concurrently
ctx.capabilities  # what this model supports
ctx.scratch_dir   # ephemeral, writable
ctx.assets_dir    # suite-owned blobs, staged before egress is cut
# no network, no credentials, no filesystem access to the weights
```

and returns a `SuiteResult`. It declares eligibility up front in a
`SuiteManifest`, so an ineligible suite is skipped **before** the model is
loaded and costs zero GPU time.

`suites/stub_capability` and `suites/stub_safety` are working examples of the
shape, not real benchmarks. The safety stub measures over-refusal on benign
prompts deliberately — it is a genuine signal that needs no adversarial content
to live in this repo. **Adversarial probes must never be committed here**: they
are held-out by design, never shipped to creators for pre-submission testing,
and only ever execute inside the no-egress sandbox.

---

## Seams kept open for VLM

Six decisions taken now so vision support is additive later. All are live and
tested; none of them do anything yet.

1. `modality` is first-class on the subject, the suite manifest, and the suite
   result. VLMs are **detected and refused**, not silently half-served.
2. `SuiteManifest.assets` — suites can carry blobs (vision fixtures) that get
   staged before egress is cut.
3. `ServingProfile.processor` — null for text; holds image size, tiling, and
   placeholder-token config for VLMs.
4. `Subject.lineage` is a list of typed `ParentEdge`s, not a single
   `derived_from` string. A VLM has several parents (base LLM, vision encoder,
   projector) under potentially different licenses.
5. `Capabilities` is one object, so `vision` / `max_images_per_request` are
   additive rather than scattered booleans.
6. The resource-class picker keys off the profile, not inline text heuristics.

Retrofitting any of these onto signed reports already in the wild means a
schema migration plus a re-sign of every report issued.

---

## What this measures about itself

`report.cost` carries the three numbers the MVP exists to produce:

- **`usd_estimate` / `gpu_seconds`** — cost per certification. Decides whether
  the trust-subscription product prices.
- **wall-clock** — printed per run.
- **`human_minutes`** — logged by hand. If this stays high, the business is
  consulting, not a platform.

Track "fraction of arbitrary models that serve with zero intervention"
separately. It starts lower than you would expect and it is the real capacity
ceiling.

---

## Known gaps

- `VLLM_SPEC` is unpinned. **Pin it** once a version is validated end to end —
  a floating engine version breaks the reproducibility the rating rests on.
  The resolved version is recorded in `report.environment` regardless.
- `_GPU_USD_PER_S` in [`cli.py`](src/keystone/cli.py) is approximate. Verify
  against current Modal pricing before quoting anyone.
- No signing yet. `Signature` exists in the schema; sigstore/cosign wiring is
  not written.
- No license-chain checking. `LicenseInfo.chain_ok` is always `None`.
- `_grade()` is placeholder logic. The real rubric belongs to the harness side.
- The `evaluate` container has no reachable network; if a suite needs an
  external resource it must arrive via `assets_dir`.
