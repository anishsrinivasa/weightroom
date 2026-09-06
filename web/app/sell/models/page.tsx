"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { StatusPill } from "@/components/StatusPill";
import { clientUploadUrl, keystoneRequest } from "@/lib/api";
import {
  type ListingState,
  type ListingSummary,
  sellerListingsSchema,
} from "@/lib/contracts";
import {
  EVALUATION_POLL_INTERVAL_MS,
  evaluationStates,
  formatDate,
  formatUsdc,
  shortDigest,
} from "@/lib/display";

const DEFAULT_COVER = "/logo.png";

type Filter = "all" | "evaluating" | Extract<ListingState, "listed" | "certified" | "rejected">;

export default function SellerModelsPage() {
  const [models, setModels] = useState<ListingSummary[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestInFlight = useRef(false);

  const load = useCallback(async (background = false) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    if (!background) setLoading(true);
    try {
      const data = await keystoneRequest("/v1/seller/listings", sellerListingsSchema);
      setModels(data.listings);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unknown request error");
    } finally {
      setLoading(false);
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const hasActiveEvaluation = models.some((model) => evaluationStates.has(model.state));

  useEffect(() => {
    if (!hasActiveEvaluation) return;
    const timer = window.setInterval(
      () => void load(true),
      EVALUATION_POLL_INTERVAL_MS,
    );
    return () => window.clearInterval(timer);
  }, [hasActiveEvaluation, load]);

  const visible = useMemo(
    () => models.filter((model) => {
      if (filter === "all") return true;
      if (filter === "evaluating") return evaluationStates.has(model.state);
      return model.state === filter;
    }),
    [filter, models],
  );

  const stats = {
    total: models.length,
    published: models.filter((model) => model.state === "listed").length,
    evaluating: models.filter((model) => evaluationStates.has(model.state)).length,
    action: models.filter((model) => ["certified", "rejected", "draft"].includes(model.state)).length,
  };

  return (
    <>
      {/* Heading kept for document structure and screen readers; the page
          leads with the inventory itself rather than a display title. */}
      <h1 className="sr-only">Your open-weight models</h1>

      <section className="stats-grid" aria-label="Inventory summary">
        <Stat value={loading ? "—" : stats.total} label="Total submissions" />
        <Stat value={loading ? "—" : stats.published} label="Published" />
        <Stat value={loading ? "—" : stats.evaluating} label="In evaluation" />
        <Stat value={loading ? "—" : stats.action} label="Needs action" />
      </section>

      <section aria-labelledby="inventory-title">
        <div className="section-heading">
          <h2 id="inventory-title">Model inventory</h2>
          <div className="heading-actions">
          <label className="sr-only" htmlFor="model-filter">Filter models</label>
          <select
            id="model-filter"
            className="select-control"
            value={filter}
            onChange={(event) => setFilter(event.target.value as Filter)}
          >
            <option value="all">All models</option>
            <option value="listed">Published</option>
            <option value="certified">Verified</option>
            <option value="evaluating">In evaluation</option>
            <option value="rejected">Not verified</option>
          </select>
          <Link className="button primary" href="/sell/submit">Submit a model <span aria-hidden="true">→</span></Link>
          </div>
        </div>

        {error ? <ErrorPanel message={error} retry={() => void load()} /> : null}
        {loading ? <LoadingBlock label="Loading your models…" /> : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Model</th><th>Status</th><th>Price</th><th>Updated</th><th><span className="sr-only">Open</span></th></tr>
              </thead>
              <tbody>
                {visible.map((model) => (
                  <tr key={model.listing_id}>
                    <td>
                      <Link className="row-link" href={`/sell/models/${model.listing_id}`}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          className="row-thumb"
                          src={model.image_url ? clientUploadUrl(model.image_url) : DEFAULT_COVER}
                          alt=""
                          data-placeholder={!model.image_url}
                        />
                        <span className="row-text">
                          <strong>{model.title || "Untitled model"}</strong>
                          {model.domain_tags.length ? <span className="tag-line">{model.domain_tags.join(" · ")}</span> : null}
                          <span className="mono muted-text">{shortDigest(model.artifact_digest)}</span>
                        </span>
                      </Link>
                    </td>
                    <td><StatusPill state={model.state} /></td>
                    <td>{formatUsdc(model.price_minor)}</td>
                    <td>{formatDate(model.updated_at)}</td>
                    <td><Link className="arrow-link" aria-label={`Open ${model.title || "model"}`} href={`/sell/models/${model.listing_id}`}>→</Link></td>
                  </tr>
                ))}
                {!visible.length ? <tr><td className="empty-cell" colSpan={5}>No models in this view.</td></tr> : null}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Stat({ value, label }: { value: number | string; label: string }) {
  return <div className="stat"><strong>{value}</strong><span>{label}</span></div>;
}
