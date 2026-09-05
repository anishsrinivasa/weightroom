# Running Keystone locally

Everything here runs with **no GPU, no Modal account, and no spend**. Only
`keystone certify` and `keystone batch` touch paid infrastructure.

---

## 1. Setup (once)

You need Python 3.12 — 3.13+ has no torch or vLLM wheels yet, and the Modal
client is fussy about newer versions too.

```bash
git clone https://github.com/anishsrinivasa/farmersmarket.git keystone
cd keystone

pip install uv                # if you don't have it
uv venv --python 3.12
uv pip install -e .
```

Check it works:

```bash
.venv/Scripts/python.exe -m pytest -q     # Windows
# .venv/bin/python -m pytest -q           # macOS / Linux
```

You should see **287 passed**. If that runs, everything below will.

> The commands below use `.venv/Scripts/python.exe -m keystone.cli` so you never
> have to activate anything. If you'd rather activate the venv
> (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere),
> the `keystone` command works directly.

---

## 2. Open the frontend

Two commands. The first fills the database so there is something to look at;
the second starts the server.

```bash
# a stable signing key, so seeded reports verify against the running server
python -c "from keystone.signing import Ed25519Signer; print(Ed25519Signer.generate().private_key_b64())" > .keystone-key

# Windows (PowerShell)
$env:KEYSTONE_SIGNING_KEY = (Get-Content .keystone-key)
.venv\Scripts\python.exe -m keystone.cli seed
.venv\Scripts\python.exe -m keystone.cli serve

# macOS / Linux
export KEYSTONE_SIGNING_KEY="$(cat .keystone-key)"
.venv/bin/python -m keystone.cli seed
.venv/bin/python -m keystone.cli serve
```

Then open **<http://127.0.0.1:8000>**.

API docs are at `/docs`. Stop the server with Ctrl-C.

If you skip the signing key, everything still works — the server just generates
a throwaway key each start, so seeded reports won't verify against it.

### Starting over

```bash
rm keystone.db          # delete the database
keystone seed           # repopulate
```

---

## 3. What to click

The **token** dropdown at the top is the whole demo. It changes who you are,
and the page reacts.

**See redaction working.** Open *Legalese-7B* in the catalogue, then switch
tokens:

| Token | Held-out score | Failing category | Cost |
| :--- | :--- | :--- | :--- |
| anonymous | `redacted` | hidden | hidden |
| `dev-creator` | `redacted` | shown | hidden |
| `dev-admin` | `0.94` | shown | shown |

Same report, three views. The audience comes from the token, never from the
URL — adding `?audience=internal` does nothing.

**Watch a payment settle.** Open a priced listing and click **Buy**. A payment
panel appears with a network, an address, and a confirmation counter. Click
**Pay from wallet** — a transaction is broadcast on the simulated chain and
confirmations accrue one block at a time until the charge settles, at which
point the download unlocks.

**Underpay** on the same panel sends too little: the transaction confirms, the
charge never settles, and the download stays refused. Same flow for the
certification fee on the Publish tab.

The simulated chain is the only piece that differs from production. Blocks come
from a clock instead of a chain watcher; everything above it — polling the
provider, waiting on confirmations, refusing to take the client's word — is the
real path.

**Upload a model.** Publish tab → **pick folder** (or **use a sample** if you
don't have a checkpoint handy). Your browser hashes each file with WebCrypto,
computes the artifact digest, and uploads straight to storage via presigned
URLs — the weights never pass through the API, which is what makes multi-GB
models possible at all. Upload the same files twice and the second declare
comes back `already_stored`, which is content-addressed dedup surfacing to the
creator as "instant".

Whole-file hashing needs the file in memory, so the browser path caps at 64 MB
per file. Real checkpoints go through the CLI.

**See a free model.** *Tokenizer-Bench-0.5B* is priced at zero and downloads
directly. Zero is a real price.

**See a rejection.** Switch the state filter to `rejected` and open
*Sentinel-1B*. As `dev-creator` you see which category failed; as anyone else
you don't.

**See the review queue.** Switch to `dev-admin` and open the **admin** tab.
*Nudged-2B* is flagged for monotonic score creep across three attempts — the
signature of someone hill-climbing the eval set rather than fixing a model.

**See the signature.** Every report has a signature block. The public key is at
<http://127.0.0.1:8000/v1/signing-key>, and it verifies:

```bash
.venv/Scripts/python.exe - <<'EOF'
import base64, json, urllib.request
from keystone.schema import CertificationReport
from keystone.signing import verify

pub = json.load(urllib.request.urlopen("http://127.0.0.1:8000/v1/signing-key"))
lid = json.load(urllib.request.urlopen("http://127.0.0.1:8000/v1/listings"))["listings"][0]["listing_id"]
req = urllib.request.Request(f"http://127.0.0.1:8000/v1/listings/{lid}",
                             headers={"Authorization": "Bearer dev-admin"})
report = CertificationReport.model_validate(json.load(urllib.request.urlopen(req))["report"])

key = base64.b64decode(pub["public_key"])
print("verifies       :", verify(report, key))
report.rating.grade = "A+"
print("after tampering:", verify(report, key))
EOF
```

---

## 4. Everything else

```bash
keystone suites       # what evaluation suites are discoverable
keystone schema       # regenerate schemas/report.schema.json
keystone worker --once  # certify anything queued (needs Modal)
```

### Free: run suites against any model

`smoke` points the suites at any OpenAI-compatible endpoint. No Modal, no GPU,
no spend. This is how the evaluation side should develop.

```bash
keystone smoke --endpoint http://localhost:8080/v1 --model my-model
```

Works with llama.cpp's `llama-server`, LM Studio, Ollama, or a hosted API.
It is **not** a certification — nothing is fetched, hashed, or scanned, so the
report is stamped `sandboxed=false` and forced to `unrated`. Held-out suites
are refused outright in this mode, because an external endpoint sees every
prompt you send it.

### Paid: a real certification

Needs a Modal account (`modal setup`). A small model costs roughly two cents.

```bash
keystone certify Qwen/Qwen2.5-0.5B-Instruct
keystone batch models.txt        # many models, reports yield
```

Gated repos (Llama etc.) need a token:

```bash
modal secret create huggingface HF_TOKEN=hf_...
export KEYSTONE_HF_SECRET=huggingface
```

---

## Troubleshooting

**`ModuleNotFoundError: keystone`** — `uv pip install -e .` wasn't run, or you
are using system Python instead of the venv one.

**Port 8000 in use** — `keystone serve --port 8001`.

**Catalogue is empty** — run `keystone seed`.

**Signature says "unsigned"** — the report was written before a signing key was
configured. Delete `keystone.db`, set `KEYSTONE_SIGNING_KEY`, and re-seed.

**`torch` / `vllm` won't install** — you're on Python 3.13+. Rebuild the venv
with `--python 3.12`. These are only needed for real certification runs, not
for the frontend.
