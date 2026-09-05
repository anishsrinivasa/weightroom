# Deploying Keystone

Two processes from one image: the **API** (serves the site and the REST API) and
the **worker** (drains `pending_certification`). They share a database and an
artifact store; the worker additionally needs Modal credentials because that is
where certification actually runs.

```
        ┌─────────┐        ┌────────────┐        ┌─────────┐
 users ─│   API   │────────│  Postgres  │────────│ worker  │──▶ Modal (GPU)
        └────┬────┘        └────────────┘        └────┬────┘
             └──────────── R2 (artifacts) ────────────┘
```

---

## Production refuses to start unsafely

`KEYSTONE_ENV=production` will not boot while any development stand-in is in
place, and the error names the variable to set:

```
KEYSTONE_ENV=production but development stand-ins are in use:
  - database: DATABASE_URL must point at Postgres; SQLite on an ephemeral
    disk loses every listing on restart.
  - artifacts: KEYSTONE_BUCKET (plus R2_ENDPOINT_URL, R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY) must be set; uploaded weights have no upstream to
    re-fetch from.
  - signing: KEYSTONE_SIGNING_KEY must be set; a key generated at boot makes
    every previously issued report fail verification.
  - auth: KEYSTONE_JWKS_URL, KEYSTONE_JWT_ISSUER and KEYSTONE_JWT_AUDIENCE
    must be set; static tokens are guessable and grant admin.
  - payments: A real payment provider must be wired; the demo provider settles
    any charge on request, so anyone could take the catalogue for free.
```

That last one is the reason the guard exists. `DemoChainProvider` settles a
charge whenever it is asked to — shipping it would hand away every paid model.
There is currently **no real payment provider wired**, so a genuine production
deploy is blocked until one is. Everything else can be configured today.

The dev-only endpoints (`/v1/dev-upload`, `/v1/charges/{id}/demo-pay`) are
mounted only when their dev backends are in use, so a production wiring cannot
expose them even by mistake. Tests assert both directions.

---

## 1. Accounts

| Service | For | Notes |
| :--- | :--- | :--- |
| **Neon** or Fly Postgres | database | any Postgres works |
| **Cloudflare R2** | artifacts | zero egress fees, which matters when the product is multi-GB downloads |
| **Modal** | certification | worker only |
| **Clerk** / Supabase / Auth0 | identity | must issue JWTs with a JWKS endpoint |
| **Fly.io** or Railway | hosting | |

## 2. Signing key

Generate once and keep it. Rotating it invalidates every report already issued.

```bash
python -c "from keystone.signing import Ed25519Signer; print(Ed25519Signer.generate().private_key_b64())"
```

Store it in a secret manager. Anyone holding it can mint reports that verify —
a KMS-backed `Signer` is the right long-term answer, and the interface already
allows one.

## 3. Environment

```bash
KEYSTONE_ENV=production

DATABASE_URL=postgres://...            # postgres:// is fine, driver is added
KEYSTONE_BUCKET=keystone-artifacts
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

KEYSTONE_SIGNING_KEY=<base64 from step 2>

KEYSTONE_JWKS_URL=https://<idp>/.well-known/jwks.json
KEYSTONE_JWT_ISSUER=https://<idp>
KEYSTONE_JWT_AUDIENCE=keystone

# worker only
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...
KEYSTONE_HF_SECRET=huggingface        # only for gated models
```

Your identity provider must put `keystone_admin: true` (boolean, not a string)
on the tokens of anyone who should reach the review queue.

## 4. Deploy

### Fly

```bash
fly launch --no-deploy
fly secrets set DATABASE_URL=... KEYSTONE_SIGNING_KEY=... KEYSTONE_BUCKET=... \
  R2_ENDPOINT_URL=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
  KEYSTONE_JWKS_URL=... KEYSTONE_JWT_ISSUER=... KEYSTONE_JWT_AUDIENCE=...
fly deploy
fly scale count app=1 worker=1
fly logs
```

### Railway

Point it at the repo; `Procfile` defines both processes. Set the same variables
in the dashboard and scale `worker` to one instance.

### Anywhere else

```bash
docker build -t keystone .
docker run -p 8000:8000 --env-file .env keystone
docker run --env-file .env keystone python -m keystone.cli worker --interval 30
```

## 5. Verify

```bash
curl https://<host>/v1/health          # {"ok": true}
curl https://<host>/v1/signing-key     # the key reports verify against
curl https://<host>/v1/benchmarks      # the menu
```

Then confirm a signed report verifies against the deployed key — the check in
[RUNNING.md](RUNNING.md) works against any host.

---

## Notes

**Schema.** `create_all()` runs at boot, which creates missing tables and does
nothing else. It will not alter an existing column. Add Alembic before the first
schema change reaches real data.

**The worker is light.** Certification runs on Modal, so this image needs the
Modal client rather than torch — a few hundred MB, not several GB.

**Scaling.** The API is stateless; run as many as you like. The worker claims
work by reading `pending_certification`, which has no locking, so **run exactly
one** until that changes. Two workers would certify the same listing twice.

**Cost.** The API and worker idle cheaply. GPU time is the real spend and it is
on Modal, metered per certification and recorded in `report.cost`.
