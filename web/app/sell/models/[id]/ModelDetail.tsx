"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { GatePill, StatusPill, stateLabel } from "@/components/StatusPill";
import { clientUploadUrl, keystoneRequest } from "@/lib/api";
import {
  benchmarksSchema,
  type ListingDetail,
  listingDetailSchema,
} from "@/lib/contracts";
import { formatBytes } from "@/lib/artifact";
import {
  EVALUATION_POLL_INTERVAL_MS,
  evaluationProgress,
  evaluationStates,
  formatDateTime,
  formatUsdc,
  rejectionDetail,
} from "@/lib/display";

const DEFAULT_COVER = "/logo.png";

const activationSchema = z.object({
  listing_id: z.string(),
  state: z.literal("listed"),
  published: z.literal(true),
});

const deactivationSchema = z.object({
  listing_id: z.string(),
  state: z.literal("certified"),
  published: z.literal(false),
});

function describeGateProgress(gate: {
  kind?: string;
  status: string;
  completed?: number;
  total?: number;
  percent?: number;
}): string {
  // A conditioned gate runs only if the capability probe says the domain is
  // worth testing, and the probe has not reported yet. Counts are withheld
  // deliberately -- the item budget encodes the capability band.
  if (gate.kind === "conditioned") {
    if (gate.status === "conditional") return "Runs only if the capability probe activates this domain.";
    if (gate.status === "not_required") return "Not required: the model showed too little capability in this domain.";
    return `${gate.percent ?? 0}% complete.`;
  }
  if (gate.status === "not_required") return "Not required: no capability probe cleared the assessment floor.";
  if (!gate.total) return "Waiting for the evaluation worker.";
  return `${Math.min(gate.completed ?? 0, gate.total)} of ${gate.total} evaluation steps completed.`;
}

export function ModelDetail({ id }: { id: string }) {
  const [model, setModel] = useState<ListingDetail | null>(null);
  const [benchmarkNames, setBenchmarkNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestInFlight = useRef(false);

  const load = useCallback(async (background = false) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    if (background) setRefreshing(true);
    else setLoading(true);
    try {
      setModel(await keystoneRequest(`/v1/listings/${encodeURIComponent(id)}`, listingDetailSchema));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unknown request error");
    } finally {
      setLoading(false);
      setRefreshing(false);
      requestInFlight.current = false;
    }
  }, [id]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void keystoneRequest("/v1/benchmarks", benchmarksSchema)
      .then((data) => {
        if (!cancelled) {
          setBenchmarkNames(Object.fromEntries(
            data.benchmarks.map((benchmark) => [benchmark.suite_id, benchmark.display_name]),
          ));
        }
      })
      // A missing catalogue should not hide the persisted selection; the suite
      // id remains a usable fallback label.
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const activeEvaluation = model ? evaluationStates.has(model.state) : false;

  useEffect(() => {
    if (!activeEvaluation) return;
    const timer = window.setInterval(
      () => void load(true),
      EVALUATION_POLL_INTERVAL_MS,
    );
    return () => window.clearInterval(timer);
  }, [activeEvaluation, load]);

  async function publish() {
    setPublishing(true);
    setError(null);
    try {
      await keystoneRequest(
        `/v1/seller/listings/${encodeURIComponent(id)}/activate`,
        activationSchema,
        { method: "POST" },
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Publishing failed");
    } finally {
      setPublishing(false);
    }
  }

  async function unlist() {
    setPublishing(true);
    setError(null);
    try {
      await keystoneRequest(
        `/v1/seller/listings/${encodeURIComponent(id)}/deactivate`,
        deactivationSchema,
        { method: "POST" },
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unlisting failed");
    } finally {
      setPublishing(false);
    }
  }

  if (loading) return <LoadingBlock label="Loading the private model record…" />;
  if (error && !model) return <ErrorPanel message={error} retry={() => void load()} />;
  if (!model) return null;

  const verified = model.state === "certified" || model.state === "listed";
  const rejected = model.state === "rejected";
  const evaluationFailed = rejected
    && model.evaluation_progress?.stage === "Evaluation failed";
  const progress = evaluationProgress(model.state);
  const report = model.report;
  const reportedBenchmarks = report?.suite_results.filter((result) => !result.gate) || [];
  const reportedBenchmarksById = new Map(
    reportedBenchmarks.map((result) => [result.suite_id, result]),
  );
  // Older completed records predate persisted benchmark selections. Preserve
  // their non-declined results while using the explicit selection for all new
  // evaluations, including the period before a report exists.
  const selectedBenchmarkIds = model.selected_benchmarks.length
    ? model.selected_benchmarks
    : reportedBenchmarks.filter((result) => !result.declined).map((result) => result.suite_id);
  const license = report?.subject.license;
  const licenseLabel = typeof license?.spdx === "string"
    ? license.spdx
    : typeof license?.declared === "string" ? license.declared : "Undeclared";
  const parents = report?.subject.lineage.map((parent) => parent.ref).join(", ") || "None declared";
  const progressRecord = model.evaluation_progress ?? (activeEvaluation ? {
    percent: 0,
    stage: model.state === "pending_certification" ? "Waiting for worker" : "Preparing evaluation",
    updated_at: model.updated_at,
    gates: [
      { gate_id: "harmbench", display_name: "HarmBench harmful-output resistance", status: "pending", completed: 0, total: 400, score: null },
      { gate_id: "jailbreakbench", display_name: "JailbreakBench harmful-request resistance", status: "pending", completed: 0, total: 200, score: null },
    ],
  } : undefined);
  const visibleGates = model.safety_gates?.gates ?? progressRecord?.gates
    .filter((gate) => gate.kind !== "benchmark")
    .map((gate) => ({
      ...gate,
      blocking: true,
      evidence: describeGateProgress(gate),
      n_items: null,
    })) ?? [];
  const gateOverall = model.safety_gates?.overall
    ?? (visibleGates.some((gate) => gate.status === "fail")
      ? "fail"
      : visibleGates.some((gate) => gate.status === "error") ? "error" : "pending");

  // A listing with no cover falls back to the house mark rather than an empty
  // frame, so the card reads the same either way.
  const coverUrl = model.image_url ? clientUploadUrl(model.image_url) : DEFAULT_COVER;

  return (
    <>
      <Link className="back-link" href="/sell/models">← Back to models</Link>
      <section className="detail-header">
        <div className="detail-identity">
          <h1>{model.title || "Untitled model"}</h1>
          <p className="digest mono">{model.artifact_digest}</p>
          <div className="inline-meta">
            <StatusPill state={model.state} label={evaluationFailed ? "Evaluation failed" : undefined} />
            <span>{formatUsdc(model.price_minor)}</span>
          </div>
          <div className="tag-list" aria-label="Model tags">
            {model.domain_tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}
            {model.size_tag ? <span className="tag" key={model.size_tag}>{model.size_tag}</span> : null}
          </div>
          {model.description ? <p className="model-description">{model.description}</p> : null}
        </div>
        <aside className="decision-card" aria-label="Verification decision">
          {/* Decorative: the verdict is stated in the text below, so the cover
              carries no meaning a screen reader would miss. */}
          <span
            className="decision-cover"
            aria-hidden="true"
            data-placeholder={!model.image_url}
            style={{ backgroundImage: `url(${coverUrl})` }}
          />
          <span className="decision-mark" aria-hidden="true">{verified ? "✓" : evaluationFailed ? "!" : rejected ? "×" : "…"}</span>
          <div>
            <h2>{verified ? "Verified" : evaluationFailed ? "Evaluation failed" : rejected ? "Not verified" : progress?.heading || stateLabel(model.state)}</h2>
            <p>{verified
              ? model.state === "listed" ? "Published and visible in the marketplace." : "All mandatory gates passed. Ready to publish."
              : evaluationFailed ? "A processing error stopped the evaluation. No certification decision was made."
              : rejected ? rejectionDetail(model.safety_gates?.overall)
              : progress?.detail || "This submission is not currently being evaluated."}</p>
            {progress ? (
              <p className="evaluation-refresh" aria-live="polite" aria-busy={refreshing}>
                {`Last update ${formatDateTime(progressRecord?.updated_at ?? model.updated_at)} · refreshes automatically`}
              </p>
            ) : null}
            {activeEvaluation && progressRecord ? (
              <div className="evaluation-meter" aria-label={`Evaluation ${progressRecord.percent}% complete`}>
                <div><span>{progressRecord.stage}</span><strong>{progressRecord.percent}%</strong></div>
                <div className="score-track" aria-hidden="true"><span style={{ width: `${progressRecord.percent}%` }} /></div>
              </div>
            ) : null}
          </div>
          {model.state === "certified" ? (
            <button className="button primary full-width" type="button" disabled={publishing} onClick={() => void publish()}>
              {publishing ? "Publishing…" : "Publish to marketplace"}
            </button>
          ) : model.state === "listed" ? (
            <button className="button primary full-width" type="button" disabled={publishing} onClick={() => void unlist()}>
              {publishing ? "Unlisting…" : "Unlist"}
            </button>
          ) : model.state === "draft" ? (
            <Link className="button primary full-width" href={`/sell/submit?draft=${encodeURIComponent(model.listing_id)}`}>
              Resume draft
            </Link>
          ) : null}
        </aside>
      </section>

      {error ? <ErrorPanel message={error} /> : null}

      <div className="detail-grid">
        <div>
          {visibleGates.length ? (
            <section className="content-block" aria-labelledby="safety-title">
              <div className="block-heading">
                <h2 id="safety-title">Safety gates</h2>
                <GatePill status={gateOverall} />
              </div>
              <ul className="gate-list">
                {visibleGates.map((gate) => {
                  const waiting = gate.status === "pending" || gate.status === "running";
                  const score = gate.score == null ? null : Math.round(gate.score * 100);
                  return (
                    <li key={gate.gate_id} className="gate-row">
                      <span className={`gate-symbol${waiting ? " loading" : ""}`} aria-hidden="true">
                        {gate.status === "pass" ? "✓" : gate.status === "fail" ? "×" : gate.status === "error" ? "!" : ""}
                      </span>
                      <span>
                        <strong>{gate.display_name}</strong>
                        <small>{gate.evidence || "No evidence available."}</small>
                        {score != null ? (
                          <div className="gate-score-track score-track" aria-hidden="true"><span style={{ width: `${score}%` }} /></div>
                        ) : null}
                      </span>
                      <span className="gate-result">
                        {score != null ? <strong>{score}%</strong> : null}
                        <GatePill status={gate.status} />
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}

          <section className="content-block" aria-labelledby="provenance-title">
            <div className="block-heading"><h2 id="provenance-title">Artifact and provenance</h2></div>
            <dl className="metadata-list">
              <dt>License</dt><dd>{licenseLabel}</dd>
              <dt>Derived from</dt><dd>{parents}</dd>
              <dt>Files</dt><dd>{report?.subject.files.length ?? "—"}</dd>
              <dt>Total size</dt><dd>{report ? formatBytes(report.subject.total_bytes) : "—"}</dd>
            </dl>
          </section>
        </div>

        <div>
          <section className="content-block" aria-labelledby="benchmarks-title">
            <div className="block-heading"><h2 id="benchmarks-title">Selected benchmark results</h2></div>
            {selectedBenchmarkIds.length ? selectedBenchmarkIds.map((suiteId) => {
              const result = reportedBenchmarksById.get(suiteId);
              const live = progressRecord?.gates.find(
                (item) => item.kind === "benchmark" && item.gate_id === suiteId,
              );
              const score = result?.score == null ? null : Math.round(result.score * 100);
              const status = result && !result.declined
                ? result.status
                : evaluationFailed ? "error" : live?.status || "pending";
              const livePercent = live?.total
                ? Math.round(100 * Math.min(live.completed ?? 0, live.total) / live.total)
                : 0;
              const value = score == null
                ? result?.score_band || status
                : `${score}%`;
              return (
                <div className="benchmark-row" key={suiteId}>
                  <div>
                    <span>{result?.display_name || benchmarkNames[suiteId] || suiteId}</span>
                    {score == null && !result?.score_band
                      ? <span className="gate-result">
                          {activeEvaluation && live?.total
                            ? <strong>{live.completed} / {live.total}</strong>
                            : null}
                          <GatePill status={status} />
                        </span>
                      : <strong>{value}</strong>}
                  </div>
                  <div
                    className="score-track"
                    aria-label={activeEvaluation && live
                      ? `${live.display_name}: ${live.completed} of ${live.total} tasks complete`
                      : undefined}
                  ><span style={{ width: score == null ? `${livePercent}%` : `${score}%` }} /></div>
                </div>
              );
            }) : <div className="empty-cell">No public capability benchmark was selected.</div>}
          </section>

        </div>
      </div>
    </>
  );
}
