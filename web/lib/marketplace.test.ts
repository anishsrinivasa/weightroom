import { describe, expect, it } from "vitest";

import type { PublicListing } from "@/lib/contracts";
import { filterListings, type MarketplaceFilters } from "@/lib/marketplace";

const listings: PublicListing[] = [
  {
    listing_id: "legal",
    title: "Contract analyst",
    description: "Legal review",
    artifact_digest: "a".repeat(64),
    state: "listed",
    price: "50 USDC",
    price_minor: 50_000_000,
    seller_id: "law-lab",
    grade: "A",
    benchmark_scores: { capability: 0.91, reasoning: 0.84 },
    domain_tags: ["legal", "reasoning"],
    size_tag: "3b-7b",
    created_at: "2026-09-05T00:00:00Z",
  },
  {
    listing_id: "bio",
    title: "Clinical writer",
    description: "Medical summaries",
    artifact_digest: "b".repeat(64),
    state: "listed",
    price: "20 USDC",
    price_minor: 20_000_000,
    seller_id: "bio-lab",
    grade: "B",
    benchmark_scores: { capability: 0.86 },
    domain_tags: ["biology", "medicine"],
    size_tag: "1b-3b",
    created_at: "2026-09-04T00:00:00Z",
  },
];

function filters(overrides: Partial<MarketplaceFilters> = {}): MarketplaceFilters {
  return {
    query: "",
    minimumPrice: null,
    maximumPrice: null,
    domains: new Set(),
    sizes: new Set(),
    benchmarks: [],
    ...overrides,
  };
}

describe("marketplace filters", () => {
  it("searches names, descriptions, sellers, and tags", () => {
    expect(filterListings(listings, filters({ query: "medicine" }))).toEqual([listings[1]]);
    expect(filterListings(listings, filters({ query: "law-lab" }))).toEqual([listings[0]]);
  });

  it("matches any selected domain and any selected size", () => {
    expect(filterListings(listings, filters({ domains: new Set(["legal", "coding"]) }))).toEqual([listings[0]]);
    expect(filterListings(listings, filters({ sizes: new Set(["1b-3b"]) }))).toEqual([listings[1]]);
  });

  it("requires every benchmark threshold and excludes missing scores", () => {
    expect(filterListings(listings, filters({
      benchmarks: [{ benchmarkId: "capability", minimumPercent: 90 }],
    }))).toEqual([listings[0]]);
    expect(filterListings(listings, filters({
      benchmarks: [{ benchmarkId: "reasoning", minimumPercent: 80 }],
    }))).toEqual([listings[0]]);
  });

  it("applies inclusive price boundaries", () => {
    expect(filterListings(listings, filters({ minimumPrice: 20, maximumPrice: 20 }))).toEqual([listings[1]]);
  });
});
