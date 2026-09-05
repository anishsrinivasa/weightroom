# Keystone

One self-contained platform. Creators upload open-weight models; when they ask
to publish, **certification runs as the gate**. Passing lists the model, failing
sends it back with coarse feedback. Buyers download artifacts they can verify
themselves.

Concept and strategy live in [model-marketplace-design-doc.md](model-marketplace-design-doc.md).
This repo is the platform that implements it.

**Scope right now: text-only LLMs.** VLM support is an additive layer, not a
rewrite — see [Seams](#seams-kept-open-for-vlm).

---

## The publish gate

```
upload ──▶ draft ──▶ publish requested ──▶ certifying ──▶ certified ──▶ listed
                            ▲                   │
                            └──── rejected ◀─────┘
                              (coarse feedback)
```

Certification itself is three stages with three privilege levels. The split
*is* the sandbox story:

| Stage | Network | Compute | Untrusted code |
| :--- | :--- | :--- | :--- |
| `fetch` / upload | **on** | CPU | no — bytes only |
| `scan` | **off** | CPU | no — inspects, never loads |
| `evaluate` | **off** | GPU | **yes** — weights load here |

`evaluate` carries `block_network=True` and `restrict_modal_access=True`. One
control, two jobs: it contains a hostile checkpoint uploaded by a stranger, and
it stops held-out eval prompts from ever leaving the box.

Cheap gates run first. A scan failure halts the job before a GPU is ever
allocated.

### Why models get served

Certification is a one-time job, but you cannot behaviourally evaluate a model
without running inference on it. `evaluate` stands up an ephemeral vLLM server
on loopback inside the sandbox, points the suites at it, and tears it down.
**Nothing is ever served to a buyer** — Keystone sells downloadable artifacts,
not inference.

A server rather than vLLM's offline batch API because continuous batching
happens no matter how the harness writes its loop, multi-turn red-team probes
work naturally, standard frameworks speak the protocol unmodified, and suites
can be built against *any* OpenAI-compatible endpoint with no Modal account. If
suites turn out uniformly single-turn, add an `OfflineBatchClient` behind
`ModelClient` — no suite changes.

---

## The eval-set leak problem

This is the sharpest design constraint in the repo, so it's worth stating
plainly.

Certification gates listing, so a rejected creator resubmits. Every bit of
detail returned is a bit of the held-out eval set leaked, and enough bits turn
our private measurement into a public one — at which point creators hill-climb
the gate without getting safer, and the rating is worthless.

The fix is structural: **two eval sets drawn from the same distribution.**

- **Public practice suites** — published, downloadable, run locally by creators
  as often as they like. Free, instant, zero GPU cost to us. Full detail is
  returned, because there is nothing to leak.
- **Held-out certification suites** — never leave the sandbox, run only at the
  gate. Only a coarse band and a failing *category* ever come back.

Legitimate iteration happens against the public set. The held-out set verifies
the improvement generalised. That turns a leak into a product feature.

Supporting layers, all implemented in [`listing.py`](src/keystone/listing.py):

| Layer | Mechanism |
| :--- | :--- |
| Coarse feedback | [`visibility.py`](src/keystone/visibility.py) — bands, never exact scores |
| Cost per attempt | `AttemptPolicy.fee` — priced per attempt, not per listing |
| Rate limit | 6h cooldown, 5 attempts per listing |
| No free samples | Resubmitting an unchanged digest is refused |
| Rotation | Held-out slice advances every 2 attempts |
| Attack detection | `detect_probing` flags small monotonic score creep for human review |
| Teeth | `listed → pending_certification → delisted`; gaming buys a temporary listing |

### Redaction

One report, three audiences. Always call `redact()` at the boundary; never
serialise a raw report to anyone outside the platform.

| | buyer | creator | internal |
| :--- | :--- | :--- | :--- |
| Grade, provenance, methodology, environment | ✅ | ✅ | ✅ |
| Held-out exact score / metrics / findings | ❌ | ❌ | ✅ |
| Held-out coarse band | ✅ | ✅ | ✅ |
| Failing category + practice pointer | ❌ | ✅ | ✅ |
| Public-suite full detail | partial | ✅ | ✅ |
| Scan finding detail | ❌ | ✅ | ✅ |
| Cost | ❌ | ❌ | ✅ |

`assert_no_leak()` is the belt-and-braces check; it runs in tests and should
run at the API boundary too.

---

## Persistence

[`db.py`](src/keystone/db.py) — SQLAlchemy over SQLite in dev, Postgres in prod.
Same code, different URL.

Domain logic stays in [`listing.py`](src/keystone/listing.py) and
[`payments.py`](src/keystone/payments.py) as plain dataclasses with no database
in them; `db.py` only maps. The attempt policy and the state machine are the
parts worth testing hard, and they should not need a database to run.

Tables: `users`, `artifacts` (keyed by digest, so identical weights are one
row), `listings`, `attempts`, `reports`, `charges`.

Two rules that are easy to get wrong:

- **Reports are stored unredacted.** The database holds internal truth;
  `redact()` happens at the boundary on the way out. `attempts.internal_score`
  in particular is exactly the signal a prober wants and must never reach a
  creator-facing serialiser.
- **The payment provider stays authoritative.** `charges` is a mirror. Never
  settle a charge from a client callback — re-read it from the provider and
  write the answer down.

## Storage

Once creators upload to us we are the origin — there is no upstream to re-fetch
from. [`storage.py`](src/keystone/storage.py) is the system of record; the Modal
Volume is demoted to a job cache.

- Content-addressed by `artifact_digest`, so identical weights cost one copy.
- Every file verified against its manifest **on write and on read**. A mismatch
  is a hard error, not a warning — that check is what lets a buyer trust a
  download without trusting us.
- `S3Store` for R2 in production, `LocalStore` for tests. `from_env()` picks.

HuggingFace remains available as an *import convenience* (`SourceKind.HF`) for
creators pulling in their own existing repo. Nothing is stored there, and
nothing HF reports is load-bearing — we re-hash everything ourselves.

---

## Signing

A rating nobody can verify is a screenshot. [`signing.py`](src/keystone/signing.py)
signs every stored report with Ed25519, and the public key is served at
`/v1/signing-key`.

Canonicalisation matters more than the algorithm: if two honest parties
serialise the same report differently, verification fails and the mechanism is
worthless. The payload is JSON with sorted keys, no whitespace, signature field
excluded. Any edit — a bumped grade, a swapped artifact digest, a different
engine version, a flipped `sandboxed` flag — breaks it, and there is a
parametrised test for each.

**What is signed is the internal report, not a redacted view.** Redacted views
are derived and do not verify against the signature, deliberately: signing
redacted views would let anyone with a creator token mint a differently-redacted
"valid" report.

Set `KEYSTONE_SIGNING_KEY` (base64 raw Ed25519 private key) so the key survives
a restart. Dev generates an ephemeral one.

## API and worker

```bash
keystone serve            # API + placeholder UI on :8000
keystone worker --once    # drain the certification queue
```

The API ([`api.py`](src/keystone/api.py)) is thin: it validates, writes rows,
and queues. No request thread ever waits on a GPU — publishing moves a listing
to `pending_certification` and returns in milliseconds, and
[`worker.py`](src/keystone/worker.py) picks it up out of process.

Flow:

```
declare manifest ──▶ PUT bytes straight to storage ──▶ finalize
       │                  (presigned, never through us)
       ▼
   create listing ──▶ publish (mints a charge) ──▶ confirm (provider re-read)
                                                        │
                                                  pending_certification
                                                        │
                                                     worker ──▶ certified │ rejected
```

Three properties worth protecting:

- **Audience is derived from the authenticated principal**, never requested.
  `?audience=internal` does nothing; there is a test asserting it.
- **Every report leaves through `redact()` plus `assert_no_leak()`.** Belt and
  braces, because a leak here is not a bug, it is the end of the moat.
- **Settlement is re-read from the payment provider.** A client saying it paid
  is not evidence that it paid — also tested.

The UI at `/` is a deliberate placeholder: one static file, black and white, no
framework and no build step. Browse the catalogue, walk the publish flow, and
see the admin review queue. Switch the token dropdown to watch the same report
redact differently for buyer, creator, and admin.

## Payments

Crypto first — no chargebacks, which matters when the product is a file that
can be copied infinitely and reclaimed never. [`payments.py`](src/keystone/payments.py)
puts it behind a `PaymentProvider` interface so a fiat rail drops in later;
procurement at a bank pays by PO and wire and cannot send USDC, so the sovereign
tier needs one eventually.

Money is always integer minor units, and the currency carries its own precision
— USDC has six decimals, USD has two, and conflating them is a 10,000x error.
`Money` rejects floats at construction.

The fee is charged **per attempt**, not per listing: that is what prices the
cost of sampling our held-out eval set. Rate limits are checked *before*
payment, so we never take money from someone we are about to reject on cooldown.

## Setup

```bash
uv venv --python 3.12          # 3.13+ has no torch/vLLM wheels yet
uv pip install -e .
python -m pytest -q            # 84 offline tests, no GPU needed
```

Modal (for anything that actually runs a model):

```bash
modal setup
```

Public models need no HuggingFace token. For gated repos (Llama et al.):

```bash
modal secret create huggingface HF_TOKEN=hf_...
export KEYSTONE_HF_SECRET=huggingface
```

R2 (for the durable store): `KEYSTONE_BUCKET`, `R2_ENDPOINT_URL`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Unset falls back to `LocalStore`.

## Use

```bash
keystone suites                                    # what's discoverable
keystone certify Qwen/Qwen2.5-0.5B-Instruct        # full run (Modal + GPU)
keystone certify <ref> --suite stub_safety         # one suite
keystone schema                                    # regenerate the JSON Schema
```

### Free path — `keystone smoke`

Runs suites against any OpenAI-compatible endpoint. No Modal, no GPU, no spend.

```bash
keystone smoke --endpoint http://localhost:8080/v1 --model my-model
```

Point it at llama.cpp's `llama-server`, LM Studio, Ollama, or a hosted API. Use
it for demos, for suite development, and in CI.

**It is not a certification, and the code makes sure it can't pretend to be.**
Nothing is fetched, hashed, or scanned; the model runs outside our sandbox. The
report is stamped `environment.sandboxed=False` and forced to `unrated`.

Held-out suites are **refused** in this mode. An external endpoint sees every
prompt sent to it, so running held-out probes through one would burn the eval
set outright — the exact thing the whole redaction layer exists to prevent.

A real certification and a smoke run execute the same `run_suites()` code, so
the free path exercises the real thing rather than a parallel implementation
that can drift.

---

## The seam

The platform owns *how* things are measured. The harness owns *what* is
measured. They meet at two files and nowhere else:

- **[`schema.py`](src/keystone/schema.py)** — the report contract.
  Authoritative; `schemas/report.schema.json` is generated from it and a test
  fails if they drift.
- **[`suites.py`](src/keystone/suites.py)** — the suite contract.

A suite receives a `SuiteContext` and nothing else:

```python
ctx.client        # ModelClient — .chat() / .complete(), safe to call concurrently
ctx.capabilities  # what this model supports
ctx.scratch_dir   # ephemeral, writable
ctx.assets_dir    # suite-owned blobs, staged before egress is cut
# no network, no credentials, no filesystem access to the weights
```

and returns a `SuiteResult`. Eligibility is declared up front in a
`SuiteManifest`, so an ineligible suite is skipped **before** the model loads
and costs zero GPU time.

**Division of labour on the leak problem.** Harness side decides what goes in
public vs. held-out, the rotation policy, and how coarse the categories are.
Platform side builds the machinery that enforces it — redaction, attempt
tracking, cooldowns, fee hooks, rotation indices, re-cert scheduling.

`suites/stub_capability` and `suites/stub_safety` demonstrate the shape; they
are not real benchmarks. The safety stub measures over-refusal on benign prompts
deliberately — a genuine signal that needs no adversarial content in this repo.
**Adversarial probes must never be committed here.**

---

## Seams kept open for VLM

Six decisions taken now so vision support is additive. All live and tested;
none do anything yet.

1. `modality` is first-class on subject, suite manifest, and suite result. VLMs
   are **detected and refused**, not silently half-served.
2. `SuiteManifest.assets` — suites can carry vision fixtures, staged before
   egress is cut.
3. `ServingProfile.processor` — null for text; holds image size, tiling, and
   placeholder-token config for VLMs.
4. `Subject.lineage` is a list of typed `ParentEdge`s. A VLM has several parents
   (base LLM, vision encoder, projector) under potentially different licenses.
5. `Capabilities` is one object, so `vision` / `max_images_per_request` are
   additive.
6. The resource-class picker keys off the profile, not inline text heuristics.

Retrofitting any of these onto signed reports already in the wild means a schema
migration plus a re-sign of every report issued.

---

## What this measures about itself

`report.cost` carries the numbers the MVP exists to produce: `gpu_seconds`,
`usd_estimate`, wall-clock, and a hand-logged `human_minutes`. If human minutes
stay high, this is consulting, not a platform.

Track "fraction of arbitrary models that serve with zero intervention"
separately. It starts lower than you'd expect and it's the real capacity
ceiling.

---

## Serving-plane gotchas (learned the hard way)

Both first-run failures were in the serving plane, neither in pipeline logic.
Expect that ratio to hold.

- **No runtime kernel JIT.** flashinfer compiles sampling kernels on first use,
  which needs the CUDA toolkit (`nvcc`, absent from the vLLM pip wheel) and
  sometimes network (absent by design). `VLLM_USE_FLASHINFER_SAMPLER=0` uses the
  native sampler instead — deterministic, no compiler, no egress. A rating that
  silently depends on which kernels happened to compile is not reproducible.
- **Telemetry off explicitly.** `VLLM_NO_USAGE_STATS` and `DO_NOT_TRACK`. With
  egress blocked, phoning home can only hang or fail init.
- **Keep the log tail generous.** The root cause of an engine failure sits well
  above the traceback; a short tail truncates exactly the line worth reading.

## Known gaps

- Only the crypto rail is real. `HostedCryptoProvider` is a skeleton awaiting a
  vendor; `MockPaymentProvider` backs dev and tests. No fiat rail, which caps
  you at buyers who can pay in stablecoin.
- `_GPU_USD_PER_S` in [`cli.py`](src/keystone/cli.py) is approximate. Verify
  against current Modal pricing.

- No license-chain checking. `LicenseInfo.chain_ok` is always `None`.
- `_grade()` is placeholder logic; the real rubric belongs to the harness side.
- `StaticTokenAuth` backs the dev server. `JWTAuth` is production-shaped
  (JWKS, issuer/audience/expiry checks, fixed algorithm list) but is not wired
  into `dev_app` and has no provider configured.
- The frontend is a placeholder: one static file, no build step, no framework.
- `finalize` trusts declared hashes; enforcement happens at materialize time,
  before weights are ever loaded. Fine, but it means a bad manifest is caught
  late rather than at upload.
- Re-certification of live listings is modelled in the state machine but nothing
  schedules it.
