"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { GatePill, StatusPill, stateLabel } from "@/components/StatusPill";
import { clientUploadUrl, keystoneRequest } from "@/lib/api";
import { type ListingDetail, listingDetailSchema } from "@/lib/contracts";
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

export function ModelDetail({ id }: { id: string }) {
  const [model, setModel] = useState<ListingDetail | null>(null);
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

  if (loading) return <LoadingBlock label="Loading the private model record…" />;
  if (error && !model) return <ErrorPanel message={error} retry={() => void load()} />;
  if (!model) return null;

  const verified = model.state === "certified" || model.state === "listed";
  const rejected = model.state === "rejected";
  const progress = evaluationProgress(model.state);
  const report = model.report;
  const benchmarks = report?.suite_results.filter((result) => !result.gate) || [];
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
  const visibleGates = model.safety_gates?.gates ?? progressRecord?.gates.map((gate) => ({
    ...gate,
    blocking: true,
    evidence: gate.total
      ? `${Math.min(gate.completed, gate.total)} of ${gate.total} evaluation steps completed.`
      : "Waiting for the evaluation worker.",
    n_items: null,
  })) ?? [];
  const gateOverall = model.safety_gates?.overall
    ?? (visibleGates.some((gate) => gate.status === "fail") ? "fail" : "pending");

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
            <StatusPill state={model.state} />
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
          <span className="decision-mark" aria-hidden="true">{verified ? "✓" : rejected ? "×" : "…"}</span>
          <div>
            <h2>{verified ? "Verified" : rejected ? "Not verified" : progress?.heading || stateLabel(model.state)}</h2>
            <p>{verified
              ? model.state === "listed" ? "Published and visible in the marketplace." : "All mandatory gates passed. Ready to publish."
              : rejected ? rejectionDetail(model.safety_gates?.overall)
              : progress?.detail || "This submission is not currently being evaluated."}</p>
            {progress ? (
              <p className="evaluation-refresh" aria-live="polite">
                {refreshing ? "Checking for an update…" : `Last update ${formatDateTime(progressRecord?.updated_at ?? model.updated_at)} · refreshes automatically`}
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
          ) : null}
        </aside>
      </section>

      {error ? <ErrorPanel message={error} /> : null}

      <div className="detail-grid">
        <div>
          {visibleGates.length ? (
            <section className="content-block" aria-labelledby="safety-title">
              <div className="block-heading">
                <div><p className="private-label">Seller only</p><h2 id="safety-title">Safety gates</h2></div>
                <GatePill status={gateOverall} />
              </div>
              <ul className="gate-list">
                {visibleGates.map((gate) => {
                  const waiting = gate.status === "pending" || gate.status === "running";
                  const score = gate.score == null ? null : Math.round(gate.score * 100);
                  return (
                    <li key={gate.gate_id} className="gate-row">
                      <span className={`gate-symbol${waiting ? " loading" : ""}`} aria-hidden="true">
                        {gate.status === "pass" ? "✓" : gate.status === "fail" ? "×" : ""}
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
              <dt>Artifact state</dt><dd>{stateLabel(model.state)}</dd>
            </dl>
          </section>
        </div>

        <div>
          <section className="content-block" aria-labelledby="benchmarks-title">
            <div className="block-heading"><h2 id="benchmarks-title">Selected benchmark results</h2></div>
            {benchmarks.length ? benchmarks.map((result) => {
              const score = result.score == null ? null : Math.round(result.score * 100);
              const value = result.declined ? "Not selected" : score == null ? result.score_band || result.status : `${score}%`;
              return (
                <div className="benchmark-row" key={result.suite_id}>
                  <div><span className={result.declined ? "muted-text" : undefined}>{result.display_name || result.suite_id}</span><strong>{value}</strong></div>
                  <div className="score-track" aria-hidden="true"><span style={{ width: result.declined || score == null ? 0 : `${score}%` }} /></div>
                </div>
              );
            }) : <div className="empty-cell">No public capability benchmark was selected.</div>}
          </section>

          <section className="content-block" aria-labelledby="certification-title">
            <div className="block-heading"><h2 id="certification-title">Certification</h2></div>
            <dl className="metadata-list">
              <dt>Safety</dt>
              <dd>{report?.rating.certified ? "Certified" : "Not certified"}</dd>
              <dt>Capability</dt>
              <dd>
                {!report?.rating.grade || report.rating.grade === "unrated"
                  ? "Not measured"
                  : report.rating.grade}
              </dd>
              <dt>Methodology</dt><dd>{report?.rating.methodology_version || "—"}</dd>
              <dt>Environment</dt><dd>{report ? report.environment.sandboxed ? "Sandboxed" : "Not sandboxed" : "—"}</dd>
              <dt>Signed by</dt><dd className="mono">{report?.signature?.key_id || "Pending"}</dd>
              <dt>Attempts</dt><dd>{model.attempts}</dd>
            </dl>
          </section>
        </div>
      </div>
    </>
  );
}
