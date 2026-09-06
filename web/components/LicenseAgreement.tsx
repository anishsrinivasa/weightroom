"use client";

import { useEffect, useRef, useState } from "react";

import type { LicenseTerms } from "@/lib/contracts";

/**
 * The terms a buyer accepts before an order exists.
 *
 * The text shown is the text the server sent with the listing, and the kind is
 * echoed back on purchase for the server to check against what the listing is
 * actually sold under. Rendering terms fetched separately would let a stale
 * copy be agreed to.
 *
 * Acceptance is a deliberate act: the button stays disabled until the checkbox
 * is ticked, because "you agreed by clicking Buy" is not something worth
 * relying on when the consequence is losing access to the platform.
 */
export function LicenseAgreement({
  terms,
  price,
  busy,
  onAccept,
  onCancel,
}: {
  terms: LicenseTerms;
  price: string;
  busy: boolean;
  onAccept: () => void;
  onCancel: () => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Escape closes, and focus starts inside the dialog rather than wherever
    // it happened to be on the page behind it.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    dialog.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        ref={dialog}
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="license-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="license-title">{terms.display_name}</h2>
        <p className="modal-lede">{terms.summary}</p>

        {/* One paragraph per blank line, so the disclosure about tracing a
            leaked copy is not buried in a wall of text. */}
        <div className="license-body">
          {terms.agreement.split("\n\n").map((paragraph) => (
            <p key={paragraph.slice(0, 40)}>{paragraph}</p>
          ))}
        </div>

        <label className="license-consent">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(event) => setAgreed(event.target.checked)}
          />
          <span>I have read and accept these terms.</span>
        </label>

        <div className="button-row">
          <button className="button quiet" type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className="button"
            type="button"
            disabled={!agreed || busy}
            onClick={onAccept}
          >
            {busy ? "Starting…" : `Accept and buy for ${price}`}
          </button>
        </div>
      </div>
    </div>
  );
}
