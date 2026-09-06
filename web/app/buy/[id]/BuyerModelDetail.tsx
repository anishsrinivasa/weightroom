"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { LicenseAgreement } from "@/components/LicenseAgreement";
import { VoteButtons } from "@/components/VoteButtons";
import { clientUploadUrl, keystoneRequest } from "@/lib/api";
import {
  benchmarksSchema,
  chargeSchema,
  demoPaymentSchema,
  listingDetailSchema,
  orderConfirmedSchema,
  purchaseSchema,
  tagCatalogueSchema,
  type Benchmark,
  type Charge,
  type ListingDetail,
  type Purchase,
  type TagCatalogue,
} from "@/lib/contracts";
import { formatDate, formatUsdc } from "@/lib/display";

const DEFAULT_COVER = "/logo.png";
const demoPaymentEnabled = process.env.NEXT_PUBLIC_ENABLE_DEMO_PAYMENT === "true";

export function BuyerModelDetail({ id }: { id: string }) {
  const [model, setModel] = useState<ListingDetail | null>(null);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [tags, setTags] = useState<TagCatalogue | null>(null);
  const [purchase, setPurchase] = useState<Purchase | null>(null);
  const [showLicense, setShowLicense] = useState(false);
  const [charge, setCharge] = useState<Charge | null>(null);
  const [loading, setLoading] = useState(true);
  const [purchasing, setPurchasing] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmationStarted = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [modelData, benchmarkData, tagData] = await Promise.all([
        keystoneRequest(`/v1/listings/${encodeURIComponent(id)}`, listingDetailSchema),
        keystoneRequest("/v1/benchmarks", benchmarksSchema),
        keystoneRequest("/v1/tags", tagCatalogueSchema),
      ]);
      setModel(modelData);
      setBenchmarks(benchmarkData.benchmarks);
      setTags(tagData);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load this listing");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const chargeId = charge?.charge_id;
  const settled = charge?.settled;

  useEffect(() => {
    if (!purchase || !chargeId || settled) return;
    let cancelled = false;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const current = await keystoneRequest(
          `/v1/charges/${encodeURIComponent(chargeId)}`,
          chargeSchema,
        );
        if (!cancelled) setCharge(current);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Could not read payment status");
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 1_500);
    void poll();
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [chargeId, purchase, settled]);

  useEffect(() => {
    if (!purchase || !charge?.settled || confirmationStarted.current) return;
    confirmationStarted.current = true;
    setConfirming(true);
    void keystoneRequest(
      `/v1/orders/${encodeURIComponent(purchase.order_id)}/confirm`,
      orderConfirmedSchema,
      { method: "POST" },
    ).then(() => load())
      .catch((caught: unknown) => {
        confirmationStarted.current = false;
        setError(caught instanceof Error ? caught.message : "Could not confirm the order");
      })
      .finally(() => setConfirming(false));
  }, [charge, load, purchase]);

  const benchmarksById = useMemo(
    () => new Map(benchmarks.map((benchmark) => [benchmark.suite_id, benchmark])),
    [benchmarks],
  );
  const domainNames = useMemo(
    () => new Map(tags?.domains.map((tag) => [tag.id, tag.label])),
    [tags],
  );
  const sizeName = tags?.model_sizes.find((tag) => tag.id === model?.size_tag)?.label;

  async function beginPurchase() {
    // The kind is echoed back from what the server sent with this listing, so
    // the terms shown and the terms checked are the same object. A boolean
    // would let a stale render agree to something else.
    const acceptedKind = model?.license?.kind;
    setPurchasing(true);
    setError(null);
    try {
      const nextPurchase = await keystoneRequest(
        `/v1/listings/${encodeURIComponent(id)}/purchase`,
        purchaseSchema,
        {
          method: "POST",
          body: JSON.stringify({ accept_license: acceptedKind }),
        },
      );
      setShowLicense(false);
      setPurchase(nextPurchase);
      setCharge(await keystoneRequest(
        `/v1/charges/${encodeURIComponent(nextPurchase.charge_id)}`,
        chargeSchema,
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start this purchase");
    } finally {
      setPurchasing(false);
    }
  }

  async function demoPay() {
    if (!purchase) return;
    setBroadcasting(true);
    setError(null);
    try {
      await keystoneRequest(
        `/v1/charges/${encodeURIComponent(purchase.charge_id)}/demo-pay`,
        demoPaymentSchema,
        { method: "POST", body: "{}" },
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Demo payment failed");
      setBroadcasting(false);
    }
  }

  async function copyAddress() {
    if (!charge) return;
    await navigator.clipboard.writeText(charge.address);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }

  if (loading && !model) return <LoadingBlock label="Loading model details…" />;
  if (error && !model) return <ErrorPanel message={error} retry={() => void load()} />;
  if (!model) return null;

  const parameterCount = model.report?.serving_profile.parameter_count;
  const architecture = model.report?.serving_profile.architecture;
  const coverUrl = model.image_url ? clientUploadUrl(model.image_url) : DEFAULT_COVER;
  const canDownload = model.entitled || model.is_owner || model.price_minor === 0;

  return (
    <>
      <Link className="back-link" href="/buy">← Back to marketplace</Link>
      <div className="buyer-detail-layout">
        <article className="buyer-model">
          <div className="buyer-cover">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={coverUrl} alt="" data-placeholder={!model.image_url} />
          </div>
          <header className="buyer-title">
            <h1>{model.title || "Untitled model"}</h1>
            {model.description ? <p className="lede">{model.description}</p> : null}
            <VoteButtons listingId={model.listing_id} tally={model.votes} />
            <div className="tag-list">
              {model.domain_tags.map((tag) => <span className="tag" key={tag}>{domainNames.get(tag) ?? tag}</span>)}
              {sizeName ? <span className="tag">{sizeName}</span> : null}
            </div>
          </header>

          <section className="content-block" aria-labelledby="buyer-benchmarks-title">
            <div className="block-heading"><h2 id="buyer-benchmarks-title">Public benchmark results</h2></div>
            {Object.entries(model.benchmark_scores).length ? Object.entries(model.benchmark_scores).map(([benchmarkId, score]) => {
              const percent = Math.round(score * 100);
              const benchmark = benchmarksById.get(benchmarkId);
              return (
                <div className="benchmark-row" key={benchmarkId}>
                  <div><span>{benchmark?.display_name ?? benchmarkId}{benchmark?.score_direction === "lower" ? <small>Lower is safer</small> : null}</span><strong>{percent}%</strong></div>
                  <div className="score-track" aria-hidden="true"><span style={{ width: `${percent}%` }} /></div>
                </div>
              );
            }) : <div className="empty-cell">No public benchmark scores are available.</div>}
          </section>

          <section className="content-block" aria-labelledby="buyer-about-title">
            <div className="block-heading"><h2 id="buyer-about-title">Model details</h2></div>
            <dl className="metadata-list">
              <dt>Seller</dt><dd className="mono">{model.seller_id}</dd>
              {model.source?.kind === "huggingface" ? <><dt>Source</dt><dd><a href={`https://huggingface.co/${model.source.ref}/tree/${model.source.revision ?? "main"}`} target="_blank" rel="noreferrer">{model.source.ref}</a></dd></> : null}
              <dt>Architecture</dt><dd>{architecture ?? "Not declared"}</dd>
              <dt>Parameters</dt><dd>{parameterCount == null ? "Not available" : formatParameters(parameterCount)}</dd>
              <dt>Published</dt><dd>{formatDate(model.created_at)}</dd>
              <dt>Artifact digest</dt><dd className="mono digest-value">{model.artifact_digest}</dd>
            </dl>
          </section>
        </article>

        <aside className="checkout-panel" aria-labelledby="checkout-title">
          <h2 id="checkout-title">Buy this model</h2>
          <div className="checkout-price"><strong>{formatUsdc(model.price_minor)}</strong></div>

          {error ? <ErrorPanel message={error} /> : null}

          {canDownload ? (
            <>
              <p className="entitlement-note">{model.is_owner ? "This is your listing." : model.price_minor === 0 ? "This model is free." : "Purchase confirmed."}</p>
              <a className="button primary full-width" href={`/api/keystone/v1/listings/${encodeURIComponent(id)}/download.zip`} download>Download ZIP</a>
              {model.is_owner ? <Link className="button full-width" href={`/sell/models/${model.listing_id}`}>View seller record</Link> : null}
            </>
          ) : purchase && charge ? (
            <div className="checkout-payment">
              <p>Send exactly <strong>{charge.amount}</strong> on {charge.chain}.</p>
              <div className="checkout-address"><span className="mono">{charge.address}</span><button className="text-button" type="button" onClick={() => void copyAddress()}>{copied ? "Copied" : "Copy"}</button></div>
              <div className="payment-progress" role="progressbar" aria-valuemin={0} aria-valuemax={charge.required_confirmations} aria-valuenow={charge.confirmations}><span style={{ width: `${Math.min(100, charge.confirmations / charge.required_confirmations * 100)}%` }} /></div>
              <p className="payment-note">{charge.tx_hash ? `${charge.confirmations} / ${charge.required_confirmations} confirmations` : "Waiting for payment"}</p>
              {purchase.checkout_url ? <a className="button primary full-width" href={purchase.checkout_url}>Open secure checkout</a> : null}
              {demoPaymentEnabled ? <button className="button primary full-width" type="button" disabled={broadcasting || Boolean(charge.tx_hash)} onClick={() => void demoPay()}>{broadcasting || charge.tx_hash ? "Demo payment broadcast" : "Pay from demo wallet"}</button> : null}
              {confirming ? <p className="payment-note">Payment settled. Granting access…</p> : null}
            </div>
          ) : (
            <>
              <button className="button primary full-width" type="button" disabled={purchasing} onClick={() => setShowLicense(true)}>{purchasing ? "Preparing checkout…" : `Buy for ${formatUsdc(model.price_minor)}`}</button>
              {model.license ? (
                <p className="license-note">
                  Sold under the {model.license.display_name}. {model.license.summary}
                </p>
              ) : null}
            </>
          )}

        </aside>
      </div>

      {showLicense && model.license ? (
        <LicenseAgreement
          terms={model.license}
          price={formatUsdc(model.price_minor)}
          busy={purchasing}
          onAccept={() => void beginPurchase()}
          onCancel={() => setShowLicense(false)}
        />
      ) : null}
    </>
  );
}

function formatParameters(count: number): string {
  if (count >= 1_000_000_000) return `${(count / 1_000_000_000).toFixed(count >= 10_000_000_000 ? 0 : 1)}B`;
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(0)}M`;
  return new Intl.NumberFormat("en-US").format(count);
}
