"use client";

import Link from "next/link";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { keystoneRequest, clientUploadUrl } from "@/lib/api";
import {
  artifactDeclarationSchema,
  artifactFinalizedSchema,
  benchmarksSchema,
  chargeSchema,
  confirmedSchema,
  demoPaymentSchema,
  imageStoredSchema,
  listingDetailSchema,
  listingCreatedSchema,
  quoteSchema,
  sampleModelManifestSchema,
  tagCatalogueSchema,
  type Benchmark,
  type Charge,
  type SafetyEvaluation,
  type SelectedFile,
} from "@/lib/contracts";
import {
  BROWSER_FILE_LIMIT,
  formatBytes,
  formatParameterCount,
  manifestDigest,
  normalizedRelativePath,
  safetensorsTensorSizes,
  sha256Hex,
} from "@/lib/artifact";
import { formatUsdc } from "@/lib/display";

type Step = 1 | 2 | 3 | 4;
const demoPaymentEnabled = process.env.NEXT_PUBLIC_ENABLE_DEMO_PAYMENT === "true";
// Matches the server's own ceilings, so an oversized file is refused here
// rather than after a pointless round trip.
const COVER_LIMIT_MB = 4;
const COVER_LIMIT_BYTES = COVER_LIMIT_MB * 1024 * 1024;
const DESCRIPTION_LIMIT = 4000;
const DEFAULT_COVER = "/logo.png";
const WEIGHT_EXTENSIONS = [".safetensors", ".bin", ".pt", ".pth", ".gguf", ".ckpt"];
const wizardSteps = [
  { number: 1, label: "Upload" },
  { number: 2, label: "Evaluate" },
  { number: 3, label: "Payment" },
  { number: 4, label: "Verification" },
] as const;

export function SubmitWizard({ draftId }: { draftId?: string }) {
  const [step, setStep] = useState<Step>(1);
  const [title, setTitle] = useState("My fine-tune");
  const [price, setPrice] = useState("45");
  const [description, setDescription] = useState("");
  const [domainTags, setDomainTags] = useState<Set<string>>(new Set());
  const [domainOptions, setDomainOptions] = useState<{ id: string; label: string }[]>([]);
  const [cover, setCover] = useState<File | null>(null);
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  const [coverRemoved, setCoverRemoved] = useState(false);
  const [picked, setPicked] = useState<SelectedFile[]>([]);
  const [digest, setDigest] = useState<string | null>(null);
  const [parameterCount, setParameterCount] = useState<number | null>(null);
  const [parameterCountKnown, setParameterCountKnown] = useState(false);
  const [hashing, setHashing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [safetyEvaluation, setSafetyEvaluation] = useState<SafetyEvaluation | null>(null);
  const [benchmarksLoading, setBenchmarksLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftLoading, setDraftLoading] = useState(Boolean(draftId));
  const [listingId, setListingId] = useState<string | null>(null);
  const [savedArtifactDigest, setSavedArtifactDigest] = useState<string | null>(null);
  const [pendingChargeId, setPendingChargeId] = useState<string | null>(null);
  const [running, setRunning] = useState<string[]>([]);
  const [charge, setCharge] = useState<Charge | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [sampleAvailable, setSampleAvailable] = useState(false);
  const directoryInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);
  const confirmationStarted = useRef(false);
  const chargeId = charge?.charge_id;
  const chargeSettled = charge?.settled;
  const modelWeightBytes = useMemo(() => {
    const weightBytes = picked
      .filter((file) => WEIGHT_EXTENSIONS.some((extension) => file.path.toLowerCase().endsWith(extension)))
      .reduce((total, file) => total + file.size_bytes, 0);
    return weightBytes || picked.reduce((total, file) => total + file.size_bytes, 0);
  }, [picked]);

  useEffect(() => {
    if (!draftId) return;
    let cancelled = false;
    void keystoneRequest(
      `/v1/listings/${encodeURIComponent(draftId)}`,
      listingDetailSchema,
    ).then((model) => {
      if (cancelled) return;
      if (!model.is_owner || model.state !== "draft") {
        throw new Error("Only your saved drafts can be resumed.");
      }
      if (!model.artifact?.files.length) {
        throw new Error("This draft no longer has an uploaded model artifact.");
      }
      setTitle(model.title || "My fine-tune");
      setPrice(String(model.price_minor / 1_000_000));
      setDescription(model.description || "");
      setDomainTags(new Set(model.domain_tags));
      setSelected(new Set(model.selected_benchmarks));
      setPicked(model.artifact.files);
      setDigest(model.artifact_digest);
      setSavedArtifactDigest(model.artifact_digest);
      setListingId(model.listing_id);
      setCoverPreview(model.image_url ? clientUploadUrl(model.image_url) : null);
      setCoverRemoved(false);
      setError(null);
    }).catch((caught: unknown) => {
      if (!cancelled) {
        setError(caught instanceof Error ? caught.message : "Could not load the draft");
      }
    }).finally(() => {
      if (!cancelled) setDraftLoading(false);
    });
    return () => { cancelled = true; };
  }, [draftId]);

  useEffect(() => {
    // No flag gates this. The prepared manifest is absent in deployed builds.
    // Probe only the small manifest here; never fetch model weights on mount.
    void fetch("/sample-model/files.json", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return;
        const parsed = sampleModelManifestSchema.safeParse(await response.json());
        setSampleAvailable(parsed.success);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    void keystoneRequest("/v1/tags", tagCatalogueSchema)
      .then((tagData) => setDomainOptions(tagData.domains))
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Could not load model domains"));
  }, []);

  useEffect(() => {
    const query = modelWeightBytes ? `?model_weight_bytes=${modelWeightBytes}` : "";
    void keystoneRequest(`/v1/benchmarks${query}`, benchmarksSchema)
      .then((benchmarkData) => {
        setBenchmarks(benchmarkData.benchmarks);
        setSafetyEvaluation(benchmarkData.safety_evaluation);
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Could not load benchmarks"))
      .finally(() => setBenchmarksLoading(false));
  }, [modelWeightBytes]);

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
    const capabilityTotal = benchmarks
      .filter((benchmark) => selected.has(benchmark.suite_id))
      .reduce((total, benchmark) => total + benchmark.price_minor, 0);
    return (safetyEvaluation?.price_minor ?? 0) + capabilityTotal;
  }, [benchmarks, safetyEvaluation, selected]);

  const billed = useMemo(
    () => benchmarks.filter((benchmark) => running.includes(benchmark.suite_id)),
    [benchmarks, running],
  );

  async function takeFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList).filter((file) => file.size > 0);
    if (!files.length) return;
    setError(null);
    if (files.some((file) => file.size > BROWSER_FILE_LIMIT)) {
      setError("Files over 512 MB must use the resumable CLI upload path.");
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
    setParameterCount(null);
    setParameterCountKnown(false);
    try {
      const entries: SelectedFile[] = [];
      const tensorSizes = new Map<string, number>();
      let safetensorsFound = false;
      let safetensorsValid = true;
      for (const file of files) {
        const data = await file.arrayBuffer();
        entries.push({
          path: normalizedRelativePath(file),
          size_bytes: file.size,
          sha256: await sha256Hex(data),
          blob: file,
        });
        if (file.name.toLowerCase().endsWith(".safetensors")) {
          safetensorsFound = true;
          try {
            for (const [name, size] of safetensorsTensorSizes(data)) {
              if (!tensorSizes.has(name)) tensorSizes.set(name, size);
            }
          } catch {
            safetensorsValid = false;
          }
        }
      }
      setPicked(entries);
      setDigest(await manifestDigest(entries));
      if (safetensorsFound && safetensorsValid && tensorSizes.size) {
        setParameterCount([...tensorSizes.values()].reduce((total, size) => total + size, 0));
        setParameterCountKnown(true);
      }
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

  // The installer has already hashed and staged this real checkpoint in the
  // local artifact store. Loading the small manifest here avoids routing a
  // multi-gigabyte model through browser memory merely to select it.
  async function loadSampleFiles() {
    const manifest = await fetch("/sample-model/files.json", { cache: "no-store" });
    if (!manifest.ok) throw new Error("Sample model is not installed.");
    const parsed = sampleModelManifestSchema.safeParse(await manifest.json());
    if (!parsed.success) throw new Error("Sample model manifest is invalid. Run `keystone sample-model` again.");
    const sample = parsed.data;
    setSampleAvailable(true);
    setError(null);
    setHashing(false);
    setPicked(sample.files);
    setDigest(sample.digest);
    setParameterCount(sample.parameter_count);
    setParameterCountKnown(sample.parameter_count !== null);
  }

  function toggleBenchmark(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleDomainTag(id: string) {
    setDomainTags((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function takeCover(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    if (file.size > COVER_LIMIT_BYTES) {
      return setError(`Cover image must be ${COVER_LIMIT_MB} MB or smaller.`);
    }
    setError(null);
    setCover(file);
    setCoverRemoved(false);
    // Revoked in the effect below, so switching images does not leak blob URLs.
    setCoverPreview(URL.createObjectURL(file));
  }

  function clearCover() {
    setCover(null);
    setCoverPreview(null);
    setCoverRemoved(true);
  }

  useEffect(() => {
    if (!coverPreview?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(coverPreview);
  }, [coverPreview]);

  async function preparePayment() {
    if (!digest || !picked.length) return setError("Choose model files first.");
    if (!title.trim()) return setError("Enter a model name.");
    const numericPrice = Number(price);
    if (!Number.isFinite(numericPrice) || numericPrice < 0) return setError("Enter a valid non-negative sale price.");

    setSubmitting(true);
    setError(null);
    try {
      const files = picked.map(({ path, size_bytes, sha256 }) => ({ path, size_bytes, sha256 }));
      if (digest !== savedArtifactDigest) {
        const declaration = await keystoneRequest(
          "/v1/artifacts",
          artifactDeclarationSchema,
          { method: "POST", body: JSON.stringify({ digest, files }) },
        );

        for (const [path, url] of Object.entries(declaration.upload_urls)) {
          const selectedFile = picked.find((file) => file.path === path);
          if (!selectedFile?.blob) {
            throw new Error("The selected model file is no longer available. Choose the model files again.");
          }
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
      }

      // Sent as raw bytes: the server hashes them and owns the digest, so
      // there is nothing here for the client to get wrong or lie about.
      let imageDigest: string | null = null;
      if (cover) {
        const stored = await keystoneRequest("/v1/images", imageStoredSchema, {
          method: "POST",
          body: cover,
          headers: { "Content-Type": cover.type || "application/octet-stream" },
        });
        imageDigest = stored.image_digest;
      }

      let currentListingId = listingId;
      if (currentListingId) {
        await keystoneRequest(
          `/v1/listings/${encodeURIComponent(currentListingId)}`,
          listingCreatedSchema,
          {
            method: "PATCH",
            body: JSON.stringify({
              artifact_digest: digest,
              title: title.trim(),
              description: description.trim() || null,
              ...(coverRemoved ? { image_digest: null } : imageDigest ? { image_digest: imageDigest } : {}),
              price_minor: Math.round(numericPrice * 1_000_000),
              domain_tags: Array.from(domainTags),
            }),
          },
        );
      } else {
        const listing = await keystoneRequest(
          "/v1/listings",
          listingCreatedSchema,
          {
            method: "POST",
            body: JSON.stringify({
              artifact_digest: digest,
              title: title.trim(),
              description: description.trim() || null,
              image_digest: imageDigest,
              price_minor: Math.round(numericPrice * 1_000_000),
              domain_tags: Array.from(domainTags),
            }),
          },
        );
        currentListingId = listing.listing_id;
        setListingId(currentListingId);
      }
      setSavedArtifactDigest(digest);

      let currentChargeId = pendingChargeId;
      if (!currentChargeId) {
        const quote = await keystoneRequest(
          `/v1/listings/${encodeURIComponent(currentListingId)}/publish`,
          quoteSchema,
          { method: "POST", body: JSON.stringify({ benchmarks: Array.from(selected) }) },
        );
        currentChargeId = quote.charge_id;
        setPendingChargeId(currentChargeId);
        // Use the server's normalized selection as the final billed line-up.
        setRunning(quote.running);
        setSafetyEvaluation(quote.safety_evaluation);
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

  if (draftLoading) {
    return (
      <div className="wizard">
        <Link className="back-link" href="/sell/models">← Back to My models</Link>
        <LoadingBlock label="Loading your saved draft…" />
      </div>
    );
  }

  if (draftId && !listingId) {
    return (
      <div className="wizard">
        <Link className="back-link" href="/sell/models">← Back to My models</Link>
        <ErrorPanel message={error || "Could not load the draft"} />
      </div>
    );
  }

  return (
    <div className="wizard">
      <Link className="back-link" href="/sell/models">← Back to My models</Link>
      <h1 className="sr-only">{draftId ? "Edit draft" : "Create a model"}</h1>
      <ol className="stepper" aria-label="Submission progress">
        {wizardSteps.map(({ number, label }) => (
          <li key={number} data-state={number < step ? "done" : number === step ? "current" : "upcoming"} aria-current={number === step ? "step" : undefined}>
            <strong>0{number} {label}</strong>
          </li>
        ))}
      </ol>

      {error ? <ErrorPanel message={error} /> : null}

      {step === 1 ? (
        <section aria-labelledby="upload-title">
          <h2 id="upload-title">{draftId ? "Edit draft" : "Upload weights"}</h2>
          <div className="form-grid">
            <label><span>Model name</span><input value={title} onChange={(event) => setTitle(event.target.value)} autoComplete="off" /></label>
            <label><span>Sale price (USDC)</span><input type="number" min="0" step="1" value={price} onChange={(event) => setPrice(event.target.value)} /></label>
          </div>
          <div className="cover-row">
            <div className="cover-preview">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={coverPreview ?? DEFAULT_COVER} alt="" data-placeholder={!coverPreview} />
            </div>
            <div className="cover-controls">
              <span className="field-label">Cover image</span>
              <p className="field-hint">PNG, JPEG, GIF, or WebP up to {COVER_LIMIT_MB} MB</p>
              <div className="button-row">
                <button className="button" type="button" onClick={() => coverInput.current?.click()}>
                  {coverPreview ? "Replace image" : "Choose image"}
                </button>
                {coverPreview ? <button className="button quiet" type="button" onClick={clearCover}>Remove</button> : null}
              </div>
              <input
                ref={coverInput}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                hidden
                onChange={(event) => { void takeCover(event.target.files); event.target.value = ""; }}
              />
            </div>
          </div>
          <label className="stacked-field">
            <span>Description</span>
            <textarea
              rows={4}
              maxLength={DESCRIPTION_LIMIT}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this model is tuned for, what it was trained on, and who should buy it."
            />
            <small className="field-hint">{description.length} / {DESCRIPTION_LIMIT}</small>
          </label>
          <fieldset className="tag-fieldset">
            <legend className="sr-only">Tags</legend>
            <div className="tag-picker">
              {domainOptions.map((tag) => (
                <label key={tag.id} data-selected={domainTags.has(tag.id)}>
                  <input
                    type="checkbox"
                    checked={domainTags.has(tag.id)}
                    onChange={() => toggleDomainTag(tag.id)}
                  />
                  <span>{tag.label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div
            className={`drop-zone ${dragging ? "dragging" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={dropped}
          >
            <div className="drop-zone-summary">
              {picked.length && !hashing ? (
                <>
                  <span className="field-label">Model size</span>
                  <strong>{parameterCountKnown && parameterCount != null
                    ? `${formatParameterCount(parameterCount)} parameters`
                    : "Parameter count unavailable locally"}</strong>
                </>
              ) : (
                <>
                  <strong>{hashing ? "Reading model files…" : "Drop your model folder here"}</strong>
                  <p>config.json, tokenizer files, and safetensors</p>
                </>
              )}
            </div>
            <div className="button-row upload-actions">
              <button className="button primary" type="button" onClick={() => directoryInput.current?.click()}>Choose folder</button>
              <button className="button" type="button" onClick={() => fileInput.current?.click()}>Choose files</button>
              {sampleAvailable ? <button className="button quiet" type="button" onClick={() => {
                void loadSampleFiles().catch((caught: unknown) =>
                  setError(caught instanceof Error ? caught.message : "Could not load the sample model"));
              }}>Reload sample model</button> : null}
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
          <div className="button-row actions"><button className="button primary" type="button" disabled={!picked.length || hashing} onClick={() => { setError(null); setStep(2); }}>Continue to evaluations →</button></div>
        </section>
      ) : null}

      {step === 2 ? (
        <section aria-labelledby="evaluation-title">
          <h2 id="evaluation-title">Choose benchmarks</h2>
          {benchmarksLoading ? <LoadingBlock label="Loading supported benchmarks…" /> : (
            <div className="benchmark-options">
              <label className="required-evaluation">
                <input type="checkbox" checked disabled readOnly />
                <span><strong>Safety Evaluation</strong></span>
                <b>{safetyEvaluation?.automatic_pass
                  ? "No charge"
                  : safetyEvaluation ? `≈ ${formatUsdc(safetyEvaluation.price_minor)}` : "Required"}</b>
              </label>
              {benchmarks.map((benchmark) => (
                <label key={benchmark.suite_id}>
                  <input type="checkbox" checked={selected.has(benchmark.suite_id)} onChange={() => toggleBenchmark(benchmark.suite_id)} />
                  <span className="benchmark-option-copy">
                    <span className="benchmark-option-heading">
                      <strong>{benchmark.display_name}</strong>
                      {benchmark.source_url ? (
                        <a
                          aria-label={`View ${benchmark.display_name} benchmark source`}
                          className="benchmark-source-link"
                          href={benchmark.source_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(event) => event.stopPropagation()}
                        >
                          ↗
                        </a>
                      ) : null}
                    </span>
                    <small>{benchmark.description}</small>
                  </span>
                  <b>{benchmark.price_is_estimate ? "≈ " : ""}{formatUsdc(benchmark.price_minor)}</b>
                </label>
              ))}
            </div>
          )}
          <div className="total-row"><span>Estimated evaluation cost</span><strong>≈ {evaluationTotal === 0 ? "0" : formatUsdc(evaluationTotal)}</strong></div>
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
            <div className="payment-heading"><h3>Send {charge.amount}</h3></div>
            <dl className="payment-details">
              <dt>Network</dt><dd>{charge.chain}</dd>
              <dt>Address</dt><dd className="address-value"><span className="mono">{charge.address}</span><button className="text-button" type="button" onClick={() => void copyAddress()}>{copied ? "Copied" : "Copy"}</button></dd>
              <dt>Transaction</dt><dd className="mono">{charge.tx_hash || "Waiting for an on-chain transfer"}</dd>
              <dt>Confirmations</dt><dd>{charge.confirmations} / {charge.required_confirmations}</dd>
            </dl>
            <div className="payment-progress" role="progressbar" aria-valuemin={0} aria-valuemax={charge.required_confirmations} aria-valuenow={charge.confirmations}>
              <span style={{ width: `${Math.min(100, charge.confirmations / charge.required_confirmations * 100)}%` }} />
            </div>
            {safetyEvaluation ? (
              <div className="payment-breakdown">
                <span className="breakdown-label">Covers</span>
                <ul>
                  <li>
                    <span>{safetyEvaluation.display_name}</span>
                    <b>{safetyEvaluation.price}</b>
                  </li>
                  {billed.map((benchmark) => (
                    <li key={benchmark.suite_id}>
                      <span>{benchmark.display_name}</span>
                      <b>{benchmark.price}</b>
                    </li>
                  ))}
                </ul>
                <div className="payment-total">
                  <span>Total cost</span>
                  <strong>{charge.amount}</strong>
                </div>
              </div>
            ) : null}
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
            <Link className="button primary" href={`/sell/models/${listingId}`}>View model status</Link>
            <Link className="button" href="/sell/submit">Start another submission</Link>
          </div>
        </section>
      ) : null}
    </div>
  );
}
