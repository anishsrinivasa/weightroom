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

// A gate that did not need to run and one that could not be assessed are both
// settled outcomes, and neither is a pass. Shown with the raw status string
// they read as "not_required" with an underscore; shown as a pass they claim a
// cleared bar nothing measured.
const gateLabels: Record<string, string> = {
  running: "In progress",
  not_required: "Not required",
  not_assessed: "Not assessed",
  insufficient_evidence: "Inconclusive",
};

export function GatePill({ status }: { status: string }) {
  const tone =
    status === "pass"
      ? "solid"
      : status === "pending" || status === "running"
        ? "muted"
        : status === "not_required" || status === "not_assessed"
          ? "muted"
          : "outline";
  return <span className={`status-pill ${tone}`}>{gateLabels[status] ?? status}</span>;
}

export function stateLabel(state: ListingState): string {
  return states[state].label;
}
