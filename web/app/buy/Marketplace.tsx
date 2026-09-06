"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ErrorPanel, LoadingBlock } from "@/components/AsyncState";
import { clientUploadUrl, keystoneRequest } from "@/lib/api";
import {
  benchmarksSchema,
  publicListingsSchema,
  tagCatalogueSchema,
  type Benchmark,
  type PublicListing,
  type TagCatalogue,
} from "@/lib/contracts";
import { formatUsdc } from "@/lib/display";
import {
  filterListings,
  type BenchmarkFilter,
} from "@/lib/marketplace";

const DEFAULT_COVER = "/logo.png";

export function Marketplace() {
  const [listings, setListings] = useState<PublicListing[]>([]);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [tags, setTags] = useState<TagCatalogue | null>(null);
  const [query, setQuery] = useState("");
  const [minimumPrice, setMinimumPrice] = useState("");
  const [maximumPrice, setMaximumPrice] = useState("");
  const [domains, setDomains] = useState<Set<string>>(new Set());
  const [sizes, setSizes] = useState<Set<string>>(new Set());
  const [benchmarkFilters, setBenchmarkFilters] = useState<BenchmarkFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void Promise.all([
      keystoneRequest("/v1/listings", publicListingsSchema),
      keystoneRequest("/v1/benchmarks", benchmarksSchema),
      keystoneRequest("/v1/tags", tagCatalogueSchema),
    ]).then(([listingData, benchmarkData, tagData]) => {
      setListings(listingData.listings);
      setBenchmarks(
        benchmarkData.benchmarks.filter(
          (benchmark) => !benchmark.gate && !benchmark.diagnostic && !benchmark.held_out,
        ),
      );
      setTags(tagData);
      setError(null);
    }).catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load the marketplace");
    }).finally(() => setLoading(false));
  }, []);

  const visible = useMemo(() => filterListings(listings, {
    query,
    minimumPrice: parsePrice(minimumPrice),
    maximumPrice: parsePrice(maximumPrice),
    domains,
    sizes,
    benchmarks: benchmarkFilters,
  }), [benchmarkFilters, domains, listings, maximumPrice, minimumPrice, query, sizes]);
  const filtersActive = hasActiveFilters(
    query,
    minimumPrice,
    maximumPrice,
    domains,
    sizes,
    benchmarkFilters,
  );

  function toggleFacet(setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string) {
    setter((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function addBenchmarkFilter() {
    const used = new Set(benchmarkFilters.map((filter) => filter.benchmarkId));
    const next = benchmarks.find((benchmark) => !used.has(benchmark.suite_id));
    if (next) setBenchmarkFilters((current) => [
      ...current,
      {
        benchmarkId: next.suite_id,
        thresholdPercent: 50,
        scoreDirection: next.score_direction,
      },
    ]);
  }

  function updateBenchmarkFilter(index: number, patch: Partial<BenchmarkFilter>) {
    setBenchmarkFilters((current) => current.map((filter, position) => (
      position === index ? { ...filter, ...patch } : filter
    )));
  }

  function clearFilters() {
    setQuery("");
    setMinimumPrice("");
    setMaximumPrice("");
    setDomains(new Set());
    setSizes(new Set());
    setBenchmarkFilters([]);
  }

  return (
    <>
      {error ? <ErrorPanel message={error} /> : null}
      {loading ? <LoadingBlock label="Loading the marketplace…" /> : (
        <div className="marketplace-layout">
          <aside className="filter-panel" aria-labelledby="filters-title">
            <div className="filter-heading">
              <h2 id="filters-title">Filters</h2>
              <button className="text-button" type="button" onClick={clearFilters}>Clear all</button>
            </div>

            <label className="filter-field">
              <span>Search</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, seller, or tag" />
            </label>

            <fieldset className="filter-group">
              <legend>Price (USDC)</legend>
              <div className="price-fields">
                <label><span className="sr-only">Minimum price</span><input type="number" min="0" value={minimumPrice} onChange={(event) => setMinimumPrice(event.target.value)} placeholder="Min" /></label>
                <span aria-hidden="true">—</span>
                <label><span className="sr-only">Maximum price</span><input type="number" min="0" value={maximumPrice} onChange={(event) => setMaximumPrice(event.target.value)} placeholder="Max" /></label>
              </div>
            </fieldset>

            <FacetGroup title="Domain" options={tags?.domains ?? []} selected={domains} toggle={(id) => toggleFacet(setDomains, id)} />
            <FacetGroup title="Model size" options={tags?.model_sizes ?? []} selected={sizes} toggle={(id) => toggleFacet(setSizes, id)} />

            <fieldset className="filter-group benchmark-filters">
              <legend>Benchmark scores</legend>
              <p className="field-hint">Models must meet every selected threshold.</p>
              {benchmarkFilters.map((filter, index) => (
                <div className="benchmark-filter" key={`${filter.benchmarkId}-${index}`}>
                  <select aria-label={`Benchmark ${index + 1}`} value={filter.benchmarkId} onChange={(event) => {
                    const benchmark = benchmarks.find((candidate) => candidate.suite_id === event.target.value);
                    updateBenchmarkFilter(index, {
                      benchmarkId: event.target.value,
                      scoreDirection: benchmark?.score_direction ?? "higher",
                    });
                  }}>
                    {benchmarks.map((benchmark) => <option key={benchmark.suite_id} value={benchmark.suite_id}>{benchmark.display_name}</option>)}
                  </select>
                  <label>
                    <span>{filter.scoreDirection === "lower" ? "Maximum" : "Minimum"}</span>
                    <span className="score-input"><input type="number" min="0" max="100" value={filter.thresholdPercent} onChange={(event) => updateBenchmarkFilter(index, { thresholdPercent: clampScore(event.target.value) })} /><b>%</b></span>
                  </label>
                  <button className="text-button" type="button" onClick={() => setBenchmarkFilters((current) => current.filter((_, position) => position !== index))}>Remove</button>
                </div>
              ))}
              <button className="button quiet full-width" type="button" disabled={benchmarkFilters.length >= benchmarks.length || !benchmarks.length} onClick={addBenchmarkFilter}>+ Add benchmark</button>
            </fieldset>
          </aside>

          <div className="marketplace-content">
            <section className="results-section" aria-labelledby="results-title">
              <div className="section-heading">
                <h2 id="results-title">{filtersActive ? "Search results" : "All models"}</h2>
                {filtersActive ? <span className="result-count">{visible.length} {visible.length === 1 ? "result" : "results"}</span> : null}
              </div>
              {visible.length ? <div className="model-grid">{visible.map((listing) => <ModelCard key={listing.listing_id} listing={listing} tags={tags} />)}</div> : (
                <div className="catalogue-empty"><h3>No models match these filters.</h3><button className="button" type="button" onClick={clearFilters}>Clear filters</button></div>
              )}
            </section>
          </div>
        </div>
      )}
    </>
  );
}

function FacetGroup({ title, options, selected, toggle }: {
  title: string;
  options: { id: string; label: string }[];
  selected: Set<string>;
  toggle: (id: string) => void;
}) {
  const selectionLabel = selected.size
    ? `${selected.size} selected`
    : title === "Domain" ? "All domains" : "All model sizes";

  return (
    <fieldset className="filter-group facet-filter-group">
      <legend>{title}</legend>
      <details className="facet-dropdown">
        <summary>
          <span>{selectionLabel}</span>
          <span className="dropdown-chevron" aria-hidden="true">⌄</span>
        </summary>
        <div className="facet-menu">
          <div className="facet-menu-heading">
            <span>Select {title.toLocaleLowerCase()}</span>
            {selected.size ? <button className="text-button" type="button" onClick={() => selected.forEach(toggle)}>Clear</button> : null}
          </div>
          <div className="facet-list">
            {options.map((option) => (
              <label key={option.id}>
                <input type="checkbox" checked={selected.has(option.id)} onChange={() => toggle(option.id)} />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </div>
      </details>
    </fieldset>
  );
}

function ModelCard({ listing, tags }: {
  listing: PublicListing;
  tags: TagCatalogue | null;
}) {
  const domainLabels = new Map(tags?.domains.map((tag) => [tag.id, tag.label]));
  const sizeLabel = tags?.model_sizes.find((tag) => tag.id === listing.size_tag)?.label;
  return (
    <Link className="model-card" href={`/buy/${listing.listing_id}`}>
      <div className="model-cover">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={listing.image_url ? clientUploadUrl(listing.image_url) : DEFAULT_COVER} alt="" data-placeholder={!listing.image_url} />
      </div>
      <div className="model-card-body">
        <div><h3>{listing.title || "Untitled model"}</h3><p>{listing.description || "Independently evaluated open weights."}</p></div>
        <div className="tag-list">
          {listing.domain_tags.slice(0, 3).map((tag) => <span className="tag" key={tag}>{domainLabels.get(tag) ?? tag}</span>)}
          {sizeLabel ? <span className="tag">{sizeLabel}</span> : null}
        </div>
        <dl className="card-metrics">
          <div><dt>Price</dt><dd>{formatUsdc(listing.price_minor)}</dd></div>
        </dl>
        <div className="card-footer"><span className="mono">{listing.seller_id}</span><span aria-hidden="true">→</span></div>
      </div>
    </Link>
  );
}

function parsePrice(value: string): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function clampScore(value: string): number {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(100, Math.max(0, number)) : 0;
}

function hasActiveFilters(
  query: string,
  minimumPrice: string,
  maximumPrice: string,
  domains: Set<string>,
  sizes: Set<string>,
  benchmarks: BenchmarkFilter[],
): boolean {
  return Boolean(query || minimumPrice || maximumPrice || domains.size || sizes.size || benchmarks.length);
}
