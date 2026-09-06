import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Research",
  description: "Technical notes from Weightroom on evaluating and distributing open-weight models.",
};

export default function BlogIndex() {
  return (
    <section className="research-index" aria-labelledby="research-title">
      <header className="research-index-header">
        <p className="eyebrow">Weightroom research</p>
        <h1 id="research-title">How we evaluate open models.</h1>
        <p className="lede">
          Technical notes on model capability, safety assurance, and the infrastructure
          required to make independent evaluation useful to buyers.
        </p>
      </header>

      <div className="research-list">
        <Link className="research-card" href="/blog/capability-conditioned-safety">
          <div className="research-card-meta">
            <span>Evaluation</span>
            <time dateTime="2026-09-06">September 6, 2026</time>
          </div>
          <div className="research-card-copy">
            <h2>A safety gate that scales with model capability</h2>
            <p>
              Why a fixed refusal threshold is not enough—and how Weightroom pairs
              private capability probes with domain-specific elicitation tests.
            </p>
          </div>
          <span className="research-card-arrow" aria-hidden="true">→</span>
        </Link>
      </div>
    </section>
  );
}
