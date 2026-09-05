# Running Weightroom locally

The application has two local services:

- **Seller Studio** — Next.js 16 on port 3000.
- **Keystone API** — FastAPI on port 8000. The evaluation worker is started
  separately only when you want to process the queue on Modal.

The local path uses SQLite, filesystem storage, static development identity,
and a simulated Base/USDC provider. It spends nothing.

## Prerequisites

- Python 3.12
- Node.js 22 or newer
- `uv`

```bash
git clone https://github.com/windigo216/weightroom.git
cd weightroom

uv venv --python 3.12
uv pip install --python .venv/bin/python -e .

cd web
npm ci
cd ..
```

## Verify both applications

```bash
.venv/bin/python -m pytest -q

cd web
npm run lint
npm run typecheck
npm test
npm run build
cd ..
```

The Python suite should report at least **300 passed**. The web suite should
lint and type-check cleanly, pass its tests, and produce an optimized build.

## Start the API

Generate one stable local signing key and seed the database:

```bash
python -c "from keystone.signing import Ed25519Signer; print(Ed25519Signer.generate().private_key_b64())" > .keystone-key
export KEYSTONE_SIGNING_KEY="$(<.keystone-key)"

.venv/bin/keystone seed
.venv/bin/keystone serve
```

The API is now at <http://127.0.0.1:8000>; OpenAPI documentation is at
<http://127.0.0.1:8000/docs>.

## Start Seller Studio

In another terminal:

```bash
cd web
KEYSTONE_API_URL=http://127.0.0.1:8000 \
KEYSTONE_DEV_TOKEN=dev-creator \
NEXT_PUBLIC_ENABLE_DEMO_PAYMENT=true \
npm run dev
```

Open <http://127.0.0.1:3000>. `KEYSTONE_DEV_TOKEN` is read only by the Next.js
server and is ignored by production builds. It never enters the browser bundle.

## Walk through the seller flow

1. **My models** lists every submission belonging to the seller, including
   drafts, evaluations, verified models, rejected models, and live listings.
2. Open **ReadySet-3B** to review a successful private safety report and its
   enabled publish action.
3. Open **Sentinel-1B** to see a failed harmful-output gate. It has no publish
   action and is unreachable through the public buyer API.
4. Open **New submission**, select files or use the development sample, and
   continue to benchmark selection.
5. Select optional public capability benchmarks. Safety evaluation remains
   mandatory and is not represented as an opt-out checkbox.
6. Continue to Base/USDC payment and use the development wallet. The UI polls
   provider state; only backend-confirmed settlement queues the evaluation.

Browser hashing is capped at 64 MB per file. Production checkpoint uploads use
presigned object-storage URLs and should use the resumable CLI path for very
large files.

## Run the worker

The API only queues jobs; it never runs evaluations itself. Keep this command
running in a third terminal whenever submissions should advance. The worker
invokes Modal and may incur GPU usage:

```bash
.venv/bin/keystone worker --interval 15
```

Use `.venv/bin/keystone worker --once` only to drain the jobs that are already
queued and then stop. If no worker process is running, Seller Studio will
correctly continue to show those jobs as **Queued**.

For the local filesystem store, the worker verifies the upload and stages it
through the authenticated Modal client into the private model-cache Volume.
In production, the worker gives Modal short-lived HTTPS download URLs minted
by the configured object store. Modal verifies the manifest digest, byte count,
and SHA-256 of every file before the isolated scan and GPU evaluation stages.

Other useful commands:

```bash
.venv/bin/keystone suites
.venv/bin/keystone schema
.venv/bin/keystone smoke --endpoint http://localhost:8080/v1 --model my-model
```

## Production-like local web server

After `npm run build`, run the standalone server:

```bash
cd web
PORT=3000 \
HOSTNAME=127.0.0.1 \
KEYSTONE_API_URL=http://127.0.0.1:8000 \
npm start
```

Production mode deliberately ignores `KEYSTONE_DEV_TOKEN`. Supply a secure,
HttpOnly cookie named `keystone_access_token` containing a valid JWT, or place
an authenticated reverse proxy in front of the application. See
[`DEPLOY.md`](DEPLOY.md).

## Troubleshooting

- **401 in Seller Studio:** the Next.js server has no seller session. For local
  development, set `KEYSTONE_DEV_TOKEN=dev-creator` before starting it.
- **Empty inventory:** run `.venv/bin/keystone seed` against the same
  `DATABASE_URL` used by the API.
- **Port already in use:** pass `--port 8001` to `keystone serve`, then update
  `KEYSTONE_API_URL`; use `npm run dev -- --port 3001` for the web app.
- **Unsigned report:** set `KEYSTONE_SIGNING_KEY` before both seeding and
  starting the API.
- **Python package missing:** reinstall with
  `uv pip install --python .venv/bin/python -e .`.
