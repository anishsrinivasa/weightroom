import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Research",
  description: "Technical notes from Weightroom on evaluating and distributing open-weight models.",
};

export default function BlogIndex() {
  return (
    <section className="research-index" aria-label="Research notes">
      <div className="research-list">
        <Link className="research-card" href="/blog/capability-conditioned-safety">
          <div className="research-card-meta">
            <span>Evaluation</span>
            <time dateTime="2026-09-06">September 6, 2026</time>
          </div>
          <div className="research-card-copy">
            <h2>How we built Weightroom&apos;s safety framework</h2>
            <p>
              Downloadable weights leave the marketplace&apos;s control after purchase. This is
              how Weightroom evaluates them before deciding whether they can be listed.
            </p>
          </div>
          <span className="research-card-arrow" aria-hidden="true">→</span>
        </Link>
      </div>
    </section>
  );
}
