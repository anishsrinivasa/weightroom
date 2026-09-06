"use client";

import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@clerk/nextjs";

import { keystoneRequest } from "@/lib/api";
import { voteResultSchema, type VoteTally } from "@/lib/contracts";

/**
 * Up and down on a listing, for signed-in accounts.
 *
 * The tally is optimistic: the server is the authority, but waiting for a
 * round trip to show a press makes the control feel broken on a slow
 * connection. A failed request puts the previous state back rather than
 * leaving the page claiming something the server did not record.
 */
export function VoteButtons({
  listingId,
  tally,
  compact = false,
}: {
  listingId: string;
  tally?: VoteTally;
  compact?: boolean;
}) {
  const { isSignedIn } = useAuth();
  const [state, setState] = useState<VoteTally>(
    tally ?? { up: 0, down: 0, score: 0, mine: 0 },
  );
  const [busy, setBusy] = useState(false);

  async function cast(value: 1 | -1) {
    if (busy) return;
    // Pressing the same side again withdraws, which is what a pressed toggle
    // should do -- otherwise a misclick is permanent.
    const next = state.mine === value ? 0 : value;
    const previous = state;
    setState(optimistic(state, next));
    setBusy(true);
    try {
      const result = await keystoneRequest(
        `/v1/listings/${encodeURIComponent(listingId)}/vote`,
        voteResultSchema,
        { method: "POST", body: JSON.stringify({ value: next }) },
      );
      setState({ up: result.up, down: result.down, score: result.score, mine: result.mine });
    } catch {
      setState(previous);
    } finally {
      setBusy(false);
    }
  }

  if (!isSignedIn) {
    return (
      <div className={`vote-group ${compact ? "compact" : ""}`}>
        <span className="vote-score" aria-label={`Score ${state.score}`}>{state.score}</span>
        {!compact ? (
          <Link className="vote-signin" href={{ pathname: "/account/[[...rest]]" }}>
            Sign in to vote
          </Link>
        ) : null}
      </div>
    );
  }

  return (
    <div className={`vote-group ${compact ? "compact" : ""}`}>
      <button
        type="button"
        className={`vote-button ${state.mine === 1 ? "active" : ""}`}
        aria-label="Upvote"
        aria-pressed={state.mine === 1}
        disabled={busy}
        onClick={(event) => {
          // Cards are wrapped in a link; voting must not navigate.
          event.preventDefault();
          event.stopPropagation();
          void cast(1);
        }}
      >
        ▲
      </button>
      <span className="vote-score" aria-live="polite">{state.score}</span>
      <button
        type="button"
        className={`vote-button ${state.mine === -1 ? "active" : ""}`}
        aria-label="Downvote"
        aria-pressed={state.mine === -1}
        disabled={busy}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void cast(-1);
        }}
      >
        ▼
      </button>
    </div>
  );
}

/** What the tally becomes if the server agrees, including withdrawing. */
function optimistic(current: VoteTally, next: number): VoteTally {
  const up = current.up - (current.mine === 1 ? 1 : 0) + (next === 1 ? 1 : 0);
  const down = current.down - (current.mine === -1 ? 1 : 0) + (next === -1 ? 1 : 0);
  return { up, down, score: up - down, mine: next as VoteTally["mine"] };
}
