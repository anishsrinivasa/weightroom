"use client";

import Link from "next/link";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { GatePill } from "@/components/StatusPill";
import { keystoneRequest, clientUploadUrl } from "@/lib/api";
import {
  artifactDeclarationSchema,
  artifactFinalizedSchema,
  benchmarksSchema,
  chargeSchema,
  confirmedSchema,
  demoPaymentSchema,
  listingCreatedSchema,
  quoteSchema,
  type Benchmark,
  type Charge,
  type SelectedFile,
} from "@/lib/contracts";
import {
  BROWSER_FILE_LIMIT,
  formatBytes,
  manifestDigest,
  normalizedRelativePath,
  sha256Hex,
} from "@/lib/artifact";
import { formatUsdc } from "@/lib/display";

type Step = 1 | 2 | 3 | 4;
const demoPaymentEnabled = process.env.NEXT_PUBLIC_ENABLE_DEMO_PAYMENT === "true";
const wizardSteps = [
  { number: 1, label: "Upload", detail: "Model files" },
  { number: 2, label: "Evaluate", detail: "Benchmarks" },
  { number: 3, label: "Payment", detail: "USDC" },
  { number: 4, label: "Verification", detail: "Decision" },
] as const;

export function SubmitWizard() {
  const [step, setStep] = useState<Step>(1);
  const [title, setTitle] = useState("My fine-tune");
  const [price, setPrice] = useState("45");
  const [picked, setPicked] = useState<SelectedFile[]>([]);
  const [digest, setDigest] = useState<string | null>(null);
  const [hashing, setHashing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarksLoading, setBenchmarksLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listingId, setListingId] = useState<string | null>(null);
  const [pendingChargeId, setPendingChargeId] = useState<string | null>(null);
  const [charge, setCharge] = useState<Charge | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [copied, setCopied] = useState(false);
  const directoryInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const confirmationStarted = useRef(false);
  const chargeId = charge?.charge_id;
  const chargeSettled = charge?.settled;

  useEffect(() => {
    if (!demoPaymentEnabled) return;
    void loadSampleFiles().catch(() => {
      // Absent sample is not an error worth interrupting for -- the picker
      // still works, and the button reports it if pressed deliberately.
    });
    // Runs once: choosing real files replaces this selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void keystoneRequest("/v1/benchmarks", benchmarksSchema)
      .then((data) => setBenchmarks(data.benchmarks))
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Could not load benchmarks"))
      .finally(() => setBenchmarksLoading(false));
  }, []);

  useEffect(() => {
    if (step !== 3 || !chargeId || chargeSettled) return;
    let cancelled = false;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const current = await keystoneRequest(`/v1/charges/${encodeURIComponent(chargeId)}`, chargeSchema);
        if (!cancelled) setCharge(current);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Payment status failed");
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 1_500);
    void poll();
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [chargeId, chargeSettled, step]);

  useEffect(() => {
    if (!charge?.settled || !listingId || confirmationStarted.current) return;
    confirmationStarted.current = true;
    setConfirming(true);
    setError(null);
    void keystoneRequest(
      `/v1/listings/${encodeURIComponent(listingId)}/confirm`,
      confirmedSchema,
      { method: "POST", body: JSON.stringify({ charge_id: charge.charge_id }) },
    ).then(() => setStep(4))
      .catch((caught: unknown) => {
        confirmationStarted.current = false;
        setError(caught instanceof Error ? caught.message : "Could not queue evaluation");
      })
      .finally(() => setConfirming(false));
  }, [charge, listingId]);

  const evaluationTotal = useMemo(() => {
    return benchmarks
      .filter((benchmark) => benchmark.mandatory || selected.has(benchmark.suite_id))
      .reduce((total, benchmark) => total + benchmark.price_minor, 0);
  }, [benchmarks, selected]);

  async function takeFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList).filter((file) => file.size > 0);
    if (!files.length) return;
    setError(null);
    if (files.some((file) => file.size > BROWSER_FILE_LIMIT)) {
      setError("Files over 64 MB must use the resumable CLI upload path.");
      return;
    }

    const paths = files.map(normalizedRelativePath);
    if (new Set(paths).size !== paths.length) {
      setError("Two selected files resolve to the same relative path.");
      return;
    }

    setHashing(true);
    setPicked([]);
    setDigest(null);
    try {
      const entries: SelectedFile[] = [];
      for (const file of files) {
        entries.push({
          path: normalizedRelativePath(file),
          size_bytes: file.size,
          sha256: await sha256Hex(await file.arrayBuffer()),
          blob: file,
        });
      }
      setPicked(entries);
      setDigest(await manifestDigest(entries));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not hash the selected files");
    } finally {
      setHashing(false);
    }
  }

  function inputChanged(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void takeFiles(event.target.files);
    event.target.value = "";
  }

  function dropped(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void takeFiles(event.dataTransfer.files);
  }

  // A real checkpoint rather than synthesised bytes: tiny-random Llama, ~6 MB,
  // with a genuine config, tokenizer and safetensors. Synthetic files would
  // exercise the hashing but skip everything downstream that reads the model
  // -- architecture detection, chat template, lineage, the scanners.
  async function loadSampleFiles() {
    const manifest = await fetch("/sample-model/files.json", { cache: "no-store" });
    if (!manifest.ok) throw new Error("Sample model is not installed.");
    const { files } = (await manifest.json()) as { files: string[] };

    const loaded = await Promise.all(
      files.map(async (path) => {
        const response = await fetch(`/sample-model/${path}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`Sample model file missing: ${path}`);
        return new File([await response.blob()], path);
      }),
    );
    await takeFiles(loaded);
  }

  function toggleBenchmark(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function preparePayment() {
    if (!digest || !picked.length) return setError("Choose model files first.");
    if (!title.trim()) return setError("Enter a model name.");
    const numericPrice = Number(price);
    if (!Number.isFinite(numericPrice) || numericPrice < 0) return setError("Enter a valid non-negative sale price.");

    setSubmitting(true);
    setError(null);
    try {
      let currentListingId = listingId;
      if (!currentListingId) {
        const files = picked.map(({ path, size_bytes, sha256 }) => ({ path, size_bytes, sha256 }));
        const declaration = await keystoneRequest(
          "/v1/artifacts",
          artifactDeclarationSchema,
          { method: "POST", body: JSON.stringify({ digest, files }) },
        );

        for (const [path, url] of Object.entries(declaration.upload_urls)) {
          const selectedFile = picked.find((file) => file.path === path);
          if (!selectedFile) throw new Error(`Upload manifest lost ${path}`);
          const upload = await fetch(clientUploadUrl(url), {
            method: "PUT",
            body: selectedFile.blob,
            cache: "no-store",
          });
          if (!upload.ok) throw new Error(`Upload failed for ${path} (${upload.status})`);
        }

        await keystoneRequest(
          `/v1/artifacts/${digest}/finalize`,
          artifactFinalizedSchema,
          { method: "POST", body: JSON.stringify({ digest, files }) },
        );
        const listing = await keystoneRequest(
          "/v1/listings",
          listingCreatedSchema,
          {
            method: "POST",
            body: JSON.stringify({
              artifact_digest: digest,
              title: title.trim(),
              price_minor: Math.round(numericPrice * 1_000_000),
            }),
          },
        );
        currentListingId = listing.listing_id;
        setListingId(currentListingId);
      }

      let currentChargeId = pendingChargeId;
      if (!currentChargeId) {
        const quote = await keystoneRequest(
          `/v1/listings/${encodeURIComponent(currentListingId)}/publish`,
          quoteSchema,
          { method: "POST", body: JSON.stringify({ benchmarks: Array.from(selected) }) },
        );
        currentChargeId = quote.charge_id;
        setPendingChargeId(currentChargeId);
      }
      const currentCharge = await keystoneRequest(
        `/v1/charges/${encodeURIComponent(currentChargeId)}`,
        chargeSchema,
      );
      setCharge(currentCharge);
      setStep(3);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the submission");
    } finally {
      setSubmitting(false);
    }
  }

  async function copyAddress() {
    if (!charge) return;
    await navigator.clipboard.writeText(charge.address);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }

  async function demoPay() {
    if (!charge) return;
    setBroadcasting(true);
    setError(null);
    try {
      await keystoneRequest(
        `/v1/charges/${encodeURIComponent(charge.charge_id)}/demo-pay`,
        demoPaymentSchema,
        { method: "POST", body: "{}" },
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Demo payment failed");
      setBroadcasting(false);
    }
  }

  return (
    <div className="wizard">
      <div className="wizard-header">
        <p className="eyebrow">New submission</p>
        <h1>Bring your model to market.</h1>
        <p className="lede">Upload the exact files buyers will receive. The artifact digest permanently binds evaluation results to those weights.</p>
      </div>
      <ol className="stepper" aria-label="Submission progress">
        {wizardSteps.map(({ number, label, detail }) => (
          <li key={number} data-state={number < step ? "done" : number === step ? "current" : "upcoming"} aria-current={number === step ? "step" : undefined}>
            <strong>0{number} {label}</strong><span>{detail}</span>
          </li>
        ))}
      </ol>

      {error ? <ErrorPanel message={error} /> : null}

      {step === 1 ? (
        <section aria-labelledby="upload-title">
          <h2 id="upload-title">Upload open weights</h2>
          <p className="section-copy">Select a model folder or individual files. Files are hashed locally before upload.</p>
          <div className="form-grid">
            <label><span>Model name</span><input value={title} onChange={(event) => setTitle(event.target.value)} autoComplete="off" /></label>
            <label><span>Sale price (USDC)</span><input type="number" min="0" step="1" value={price} onChange={(event) => setPrice(event.target.value)} /></label>
          </div>
          <div
            className={`drop-zone ${dragging ? "dragging" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={dropped}
          >
            <strong>Drop your model folder here</strong>
            <p>config.json, tokenizer files, and safetensors</p>
            <div className="button-row">
              <button className="button primary" type="button" onClick={() => directoryInput.current?.click()}>Choose folder</button>
              <button className="button" type="button" onClick={() => fileInput.current?.click()}>Choose files</button>
              {demoPaymentEnabled ? <button className="button quiet" type="button" onClick={() => {
                void loadSampleFiles().catch((caught: unknown) =>
                  setError(caught instanceof Error ? caught.message : "Could not load the sample model"));
              }}>Use sample model</button> : null}
            </div>
            <input ref={directoryInput} hidden type="file" multiple onChange={inputChanged} {...{ webkitdirectory: "" }} />
            <input ref={fileInput} hidden type="file" multiple onChange={inputChanged} />
          </div>
          {hashing ? <LoadingBlock label="Hashing model files locally…" /> : null}
          {picked.length && digest ? (
            <div className="table-scroll file-list">
              <table><thead><tr><th>File</th><th>Size</th><th>SHA-256</th></tr></thead>
                <tbody>{picked.map((file) => <tr key={file.path}><td>{file.path}</td><td>{formatBytes(file.size_bytes)}</td><td className="mono">{file.sha256.slice(0, 18)}…</td></tr>)}
                  <tr><td><strong>Artifact digest</strong></td><td colSpan={2} className="mono digest-cell">{digest}</td></tr>
                </tbody></table>
            </div>
          ) : null}
          <div className="button-row actions"><button className="button primary" type="button" disabled={!picked.length || hashing} onClick={() => setStep(2)}>Continue to evaluations →</button></div>
        </section>
      ) : null}

      {step === 2 ? (
        <section aria-labelledby="evaluation-title">
          <h2 id="evaluation-title">Choose public benchmarks</h2>
          <p className="section-copy">Choose the capability evidence buyers should see. Safety screening runs separately and cannot be opted out.</p>
          <div className="safety-callout">
            <span className="gate-symbol" aria-hidden="true">✓</span>
            <div><strong>Frontier safety screening</strong><p>Artifact, license, and harmful-output gates run automatically.</p></div>
            <GatePill status="required" />
          </div>
          {benchmarksLoading ? <LoadingBlock label="Loading supported benchmarks…" /> : (
            <div className="benchmark-options">
              {benchmarks.map((benchmark) => (
                <label key={benchmark.suite_id}>
                  <input type="checkbox" checked={benchmark.mandatory || selected.has(benchmark.suite_id)} disabled={benchmark.mandatory} onChange={() => toggleBenchmark(benchmark.suite_id)} />
                  <span><strong>{benchmark.display_name}</strong>{benchmark.mandatory ? <em>Required</em> : null}<small>{benchmark.description}</small></span>
                  <b>{benchmark.price}</b>
                </label>
              ))}
            </div>
          )}
          <div className="total-row"><span>Evaluation fee</span><strong>{formatUsdc(evaluationTotal)}</strong></div>
          <div className="button-row actions">
            <button className="button" type="button" onClick={() => setStep(1)}>← Back</button>
            <button className="button primary" type="button" disabled={submitting || benchmarksLoading} onClick={() => void preparePayment()}>{submitting ? "Preparing secure upload…" : "Continue to payment →"}</button>
          </div>
        </section>
      ) : null}

      {step === 3 && charge ? (
        <section aria-labelledby="payment-title">
          <h2 id="payment-title">Pay for evaluation</h2>
          <p className="section-copy">Send the exact amount below. Evaluation is queued only after provider-verified settlement.</p>
          <div className="payment-card">
            <div className="payment-heading"><h3>Send {charge.amount}</h3><GatePill status={charge.settled ? "settled" : charge.tx_hash ? "confirming" : "awaiting payment"} /></div>
            <dl className="payment-details">
              <dt>Network</dt><dd>{charge.chain}</dd>
              <dt>Address</dt><dd className="address-value"><span className="mono">{charge.address}</span><button className="text-button" type="button" onClick={() => void copyAddress()}>{copied ? "Copied" : "Copy"}</button></dd>
              <dt>Transaction</dt><dd className="mono">{charge.tx_hash || "Waiting for an on-chain transfer"}</dd>
              <dt>Confirmations</dt><dd>{charge.confirmations} / {charge.required_confirmations}</dd>
            </dl>
            <div className="payment-progress" role="progressbar" aria-valuemin={0} aria-valuemax={charge.required_confirmations} aria-valuenow={charge.confirmations}>
              <span style={{ width: `${Math.min(100, charge.confirmations / charge.required_confirmations * 100)}%` }} />
            </div>
            <p className="payment-note">The server reads settlement from the payment provider. Browser claims are never accepted as payment evidence.</p>
            {demoPaymentEnabled ? <button className="button primary" type="button" disabled={broadcasting || Boolean(charge.tx_hash)} onClick={() => void demoPay()}>{broadcasting || charge.tx_hash ? "Demo payment broadcast" : "Pay from demo wallet"}</button> : null}
            {confirming ? <p className="payment-note">Payment settled. Queueing evaluation…</p> : null}
          </div>
          <div className="button-row actions"><button className="button" type="button" onClick={() => setStep(2)} disabled={Boolean(charge.tx_hash)}>← Back</button></div>
        </section>
      ) : null}

      {step === 4 && listingId ? (
        <section className="completion-card" aria-labelledby="complete-title">
          <span className="decision-mark" aria-hidden="true">✓</span>
          <h2 id="complete-title">Submitted for verification</h2>
          <p>Payment settled and the model is queued. Safety gates and selected benchmarks appear only in your seller record.</p>
          <div className="button-row">
            <Link className="button primary" href={`/models/${listingId}`}>View model status</Link>
            <Link className="button" href="/submit">Start another submission</Link>
          </div>
        </section>
      ) : null}
    </div>
  );
}
