import type { ListingState } from "@/lib/contracts";

export const evaluationStates = new Set<ListingState>([
  "pending_certification",
  "certifying",
]);

export const EVALUATION_POLL_INTERVAL_MS = 5_000;

export function evaluationProgress(state: ListingState): {
  heading: string;
  detail: string;
} | null {
  if (state === "pending_certification") {
    return {
      heading: "Queued for evaluation",
      detail: "Your payment is confirmed. The evaluation worker has not claimed this model yet.",
    };
  }
  if (state === "certifying") {
    return {
      heading: "Evaluation in progress",
      detail: "Safety gates and selected benchmarks are running now.",
    };
  }
  return null;
}

export function rejectionDetail(safetyStatus?: string): string {
  if (safetyStatus === "fail") {
    return "At least one mandatory gate failed. This model cannot be shown to buyers.";
  }
  if (safetyStatus === "pending") {
    return "Certification could not be issued because a required safety check was unavailable or incomplete.";
  }
  return "This model did not meet the certification requirements and cannot be shown to buyers.";
}

export function formatUsdc(minor: number): string {
  if (minor === 0) return "Free";
  return `${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(minor / 1_000_000)} USDC`;
}

export function shortDigest(value: string): string {
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

function apiDate(value: string): Date {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(apiDate(value));
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(apiDate(value));
}
