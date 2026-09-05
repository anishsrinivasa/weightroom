# CLAUDE.md — Keystone

> Note: `C:\Users\anish\CLAUDE.md` belongs to an unrelated project (a 3D figure
> pipeline). Ignore it here. This file governs this repo.

## What this is

The **platform side** of Keystone: a certification pipeline for open-weight
models. Read [README.md](README.md) first, then
[model-marketplace-design-doc.md](model-marketplace-design-doc.md) for strategy.

Two people, two lanes:

- **Platform (this repo's owner)** — ingest, sandboxing, serving, orchestration,
  reproducibility, reporting.
- **Eval harness (collaborator)** — what gets measured: suites, probes, scoring,
  the rating rubric.

They meet at [`schema.py`](src/keystone/schema.py) and
[`suites.py`](src/keystone/suites.py). Do not add coupling anywhere else.

## Rules

- **Scope is text-only LLMs.** VLM support is a later additive layer. Do not add
  vision code paths; do keep the six `SEAM` markers intact and honest.
- **`evaluate` must never gain network access or credentials.** It is the stage
  where untrusted weights load. `block_network=True` and
  `restrict_modal_access=True` are load-bearing, not defaults to tidy away.
- **Never commit adversarial eval content.** Held-out probes live outside this
  repo and only execute inside the sandbox. A leaked probe set makes the rating
  worthless.
- **`schema.py` is authoritative**; `schemas/report.schema.json` is generated
  (`keystone schema`). A test enforces they match — regenerate, never hand-edit.
- **Reproducibility is a legal requirement, not a nicety.** Anything that
  affects a result (engine version, seed, chat template, GPU, container) gets
  recorded in `report.environment`. We rate and measure; we do not warrant.
- Suites talk to models only through `ModelClient`. Never hand a suite a
  filesystem path to weights.
- Gate suites on capability/modality *before* the model loads, so an ineligible
  suite costs no GPU time.

## Conventions

- Python 3.12 (3.13+ has no torch/vLLM wheels). `uv` for envs.
- Explicit types on module boundaries; pydantic at every boundary.
- Modal functions stay thin — real logic lives in importable `keystone.*`
  modules so it can be tested without Modal or a GPU.
- Tests must run with no GPU and no Modal account (`python -m pytest -q`).
- Money in integer cents anywhere it becomes user-facing.

## Commands

```bash
python -m pytest -q                          # offline tests
keystone suites                              # list discoverable suites
keystone certify <hf-repo-id>                # full pipeline
keystone schema                              # regenerate the JSON Schema
```
