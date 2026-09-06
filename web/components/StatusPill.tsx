import type { ListingState } from "@/lib/contracts";

const states: Record<ListingState, { label: string; tone: "solid" | "outline" | "muted" }> = {
  listed: { label: "Published", tone: "solid" },
  certified: { label: "Verified", tone: "solid" },
  rejected: { label: "Not verified", tone: "outline" },
  pending_certification: { label: "Queued", tone: "muted" },
  certifying: { label: "Evaluating", tone: "muted" },
  draft: { label: "Draft", tone: "muted" },
  delisted: { label: "Delisted", tone: "muted" },
  withdrawn: { label: "Withdrawn", tone: "muted" },
};

export function StatusPill({ state, label }: { state: ListingState; label?: string }) {
  const status = states[state];
  return <span className={`status-pill ${status.tone}`}>{label ?? status.label}</span>;
}

export function GatePill({ status }: { status: string }) {
  const tone = status === "pass" ? "solid" : status === "pending" || status === "running" ? "muted" : "outline";
  return <span className={`status-pill ${tone}`}>{status === "running" ? "In progress" : status}</span>;
}

export function stateLabel(state: ListingState): string {
  return states[state].label;
}
