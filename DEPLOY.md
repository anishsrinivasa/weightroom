# Production deployment

Weightroom is deployed as three processes. Only the Next.js web service is
publicly reachable.

```text
browser ──TLS──▶ Next.js Seller Studio ──private network──▶ Keystone API
                          │                                  │
                    secure session                    Postgres + R2
                                                             │
                                                     worker ─┴─▶ Modal GPU
```

The web service is a backend-for-frontend. It reads a secure identity cookie on
the server, forwards the JWT to Keystone, validates response shapes in the
browser, refuses cross-origin mutations, and sends private `no-store` responses.
Keystone remains the authorization boundary: seller ownership, report
redaction, certification, payment settlement, and publishing are enforced
there even if the UI is bypassed.

## Images

- [`Dockerfile`](Dockerfile) builds the FastAPI API/worker image.
- [`web/Dockerfile`](web/Dockerfile) builds the minimal Next.js standalone
  image as a non-root user.
- [`compose.production.yml`](compose.production.yml) documents the complete
  topology and keeps the API off the host network.

Both containers have health checks, immutable application filesystems, a
temporary `/tmp`, and `no-new-privileges` in the supplied Compose definition.
Terminate TLS at the load balancer or ingress in front of port 3000.

## Identity contract

Use Clerk, Auth0, Supabase, or another OIDC provider that issues asymmetric
JWTs. The JWT must contain:

- `sub` — stable seller identifier;
- `email` — seller email;
- `exp`, `iss`, and `aud` — validated by Keystone;
- optional `keystone_admin: true` — platform administrators only.

Your identity callback or authentication proxy must set the JWT as a cookie on
the Seller Studio origin. Recommended cookie attributes are `HttpOnly`,
`Secure`, `SameSite=Strict`, a narrow `Path=/`, and a bounded lifetime. The
cookie name defaults to `keystone_access_token` and can be changed with
`KEYSTONE_SESSION_COOKIE`.

Never expose this token through a `NEXT_PUBLIC_*` variable, local storage, or
client-side JavaScript. The development fallback `KEYSTONE_DEV_TOKEN` is
ignored when `NODE_ENV=production`.

Configure Keystone with the same issuer and audience:

```bash
KEYSTONE_JWKS_URL=https://identity.example/.well-known/jwks.json
KEYSTONE_JWT_ISSUER=https://identity.example
KEYSTONE_JWT_AUDIENCE=weightroom
```

## Required environment

### Web

```bash
NODE_ENV=production
KEYSTONE_API_URL=http://keystone-api.internal:8000
KEYSTONE_SESSION_COOKIE=keystone_access_token
```

`KEYSTONE_API_URL` is server-only. Use private service discovery and do not
publish the API service directly.

Configure the R2 bucket CORS policy to allow `PUT` from the exact Seller Studio
origin and only the headers included in the signed request. Do not use a `*`
origin with credentials. Presigned URLs should remain short-lived.

### API and worker

```bash
KEYSTONE_ENV=production
DATABASE_URL=postgres://...

KEYSTONE_BUCKET=weightroom-artifacts
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

KEYSTONE_SIGNING_KEY=<base64 Ed25519 private key>
KEYSTONE_JWKS_URL=https://identity.example/.well-known/jwks.json
KEYSTONE_JWT_ISSUER=https://identity.example
KEYSTONE_JWT_AUDIENCE=weightroom

# Worker only
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...
KEYSTONE_HF_SECRET=huggingface
```

Generate the report-signing key once and store it in a managed secret service:

```bash
python -c "from keystone.signing import Ed25519Signer; print(Ed25519Signer.generate().private_key_b64())"
```

Anyone with this value can mint apparently valid reports. A KMS-backed signer
is the long-term production target.

## Fail-closed production guard

`KEYSTONE_ENV=production` refuses to boot with SQLite, filesystem artifact
storage, ephemeral signing, static development users, or the simulated payment
provider. Development upload and payment endpoints are mounted only when their
development backends are active.

The repository still has a deliberate launch blocker: a real payment adapter
must replace `DemoChainProvider`. The current `HostedCryptoProvider` is an
interface stub, not a provider integration. Do not remove the guard to deploy;
choose the custody/payment vendor, implement its signed webhook or
authoritative status API, and test replay, underpayment, expiry, and payout
behavior first.

## Build and deploy

Build each image independently:

```bash
docker build -t weightroom-api .
docker build -t weightroom-web ./web
```

Or validate the topology after supplying all required secrets:

```bash
docker compose -f compose.production.yml config
docker compose -f compose.production.yml up --build
```

Deploy the images to Fly.io, Railway, ECS, Kubernetes, or another container
platform with private service networking. Run exactly one worker until queue
claiming gains database-level locking; multiple workers can currently evaluate
the same listing twice.

The API and worker must share the same `DATABASE_URL` and object-store settings.
The worker mints four-hour presigned GET URLs, passes them only to Modal's
network-enabled fetch function, and Modal revalidates the stored manifest and
all file hashes. Scanner and GPU functions remain network-isolated and receive
only the verified private Volume cache key.

## Release checks

Before every deployment:

```bash
.venv/bin/python -m pytest -q

cd web
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

After deployment verify:

1. `/api/health` on the public web service returns `200`.
2. The API `/v1/health` is reachable from the web/worker network but not from
   the public internet.
3. An authenticated seller sees their full inventory and private safety gates.
4. An anonymous request cannot reach `/v1/seller/listings`.
5. A rejected or certified-but-unpublished listing returns `404` to a buyer.
6. Buyer report payloads contain neither `safety_gates` nor gate-suite rows.
7. A report verifies against `/v1/signing-key` and fails verification after any
   signed field is modified.
8. Payment confirmation is rejected until the provider reports settlement.

## Operational notes

- Add Alembic before the first production schema migration. `create_all()` does
  not alter existing columns.
- Store artifacts in R2 or S3; the Modal volume is only an evaluation cache.
- CSP uses a fresh per-request nonce. Seller pages are intentionally rendered
  dynamically and private responses are not CDN-cacheable.
- Add centralized logs and metrics for request IDs, queue latency, provider
  callbacks, evaluation failures, and publication decisions before launch.
