import type { PublicListing } from "@/lib/contracts";

export type BenchmarkFilter = {
  benchmarkId: string;
  thresholdPercent: number;
  scoreDirection: "higher" | "lower";
};

export type MarketplaceFilters = {
  query: string;
  minimumPrice: number | null;
  maximumPrice: number | null;
  domains: Set<string>;
  sizes: Set<string>;
  benchmarks: BenchmarkFilter[];
};

export function filterListings(
  listings: PublicListing[],
  filters: MarketplaceFilters,
): PublicListing[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return listings.filter((listing) => {
    const searchable = [
      listing.title,
      listing.description,
      listing.seller_id,
      listing.size_tag,
      ...listing.domain_tags,
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    if (query && !searchable.includes(query)) return false;

    const price = listing.price_minor / 1_000_000;
    if (filters.minimumPrice != null && price < filters.minimumPrice) return false;
    if (filters.maximumPrice != null && price > filters.maximumPrice) return false;
    if (filters.domains.size && !listing.domain_tags.some((tag) => filters.domains.has(tag))) {
      return false;
    }
    if (filters.sizes.size && (!listing.size_tag || !filters.sizes.has(listing.size_tag))) {
      return false;
    }
    return filters.benchmarks.every(({ benchmarkId, thresholdPercent, scoreDirection }) => {
      const score = listing.benchmark_scores[benchmarkId];
      if (score == null) return false;
      return scoreDirection === "lower"
        ? score * 100 <= thresholdPercent
        : score * 100 >= thresholdPercent;
    });
  });
}
