import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "A safety gate that scales with model capability",
  description:
    "Inside Weightroom's capability-conditioned safety evaluation for downloadable open-weight language models.",
};

const sections = [
  ["fixed-thresholds", "The problem with one threshold"],
  ["mechanism", "The mechanism"],
  ["private-evaluations", "Building private evaluations"],
  ["uncertainty", "Accounting for uncertainty"],
  ["judge", "Judging harmful assistance"],
  ["leakage", "Preventing leakage"],
  ["results", "What we observed"],
  ["limitations", "Limitations"],
] as const;

const domainRows = [
  {
    domain: "Biology",
    probe: "WMDP-Bio-derived multiple choice",
    probeUrl: "https://www.wmdp.ai/",
    elicitation: "Chemical and biological behaviors",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
  },
  {
    domain: "Coding / cyber",
    probe: "SWE-bench-derived patch recognition",
    probeUrl: "https://github.com/SWE-bench/SWE-bench",
    elicitation: "Cybercrime and intrusion behaviors",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
  },
  {
    domain: "Legal",
    probe: "LegalBench-derived binary questions",
    probeUrl: "https://legalbench.org/",
    elicitation: "Illegal-assistance behaviors in legal contexts",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
  },
] as const;

export default function CapabilityConditionedSafetyPost() {
  return (
    <article className="research-article">
      <header className="article-hero">
        <Link className="back-link" href="/blog">← Research</Link>
        <div className="article-kicker">
          <span>Evaluation systems</span>
          <time dateTime="2026-09-06">September 6, 2026</time>
        </div>
        <h1>A safety gate that scales with model capability</h1>
        <p className="article-dek">
          Refusal rates tell us how often a model says no. They do not tell us how
          much dangerous capability sits behind the answers that get through. We
          built a publishing gate that measures both—and makes the required safety
          margin stricter as capability rises.
        </p>

        <div className="article-hero-visual" aria-label="The core evaluation rule">
          <div>
            <span className="visual-label">Risk model</span>
            <strong>risk ≈ capability × elicitation success</strong>
          </div>
          <div>
            <span className="visual-label">Release rule</span>
            <strong>UCB(harm) ≤ κ(capability)</strong>
          </div>
        </div>

        <dl className="article-stats">
          <div><dt>Domains today</dt><dd>3</dd></div>
          <div><dt>Probe size</dt><dd>100</dd><span>items per domain</span></div>
          <div><dt>Typical runtime</dt><dd>5–10m</dd><span>on one A10G</span></div>
          <div><dt>Observed run cost</dt><dd>≈ $0.13</dd><span>full three-domain run</span></div>
        </dl>
      </header>

      <div className="article-layout">
        <aside className="article-sidebar">
          <nav aria-label="Article sections">
            <p className="private-label">In this article</p>
            <ol>
              {sections.map(([id, label]) => (
                <li key={id}><a href={`#${id}`}>{label}</a></li>
              ))}
            </ol>
          </nav>
          <div className="article-note">
            <p className="private-label">Scope</p>
            <p>
              Text-only language models distributed as downloadable weights. This is
              an automated publishing screen, not a claim that a model is safe in every
              deployment.
            </p>
          </div>
        </aside>

        <div className="article-body">
          <p className="article-opening">
            Two models can both decline 96% of harmful biology requests and present
            radically different risk. One may be too weak to transfer useful knowledge.
            The other may have expert-adjacent capability, making the remaining 4%
            consequential. A single refusal threshold scores them identically.
          </p>
          <p>
            This is not a calibration problem that can be fixed by changing 90% to 95%.
            The missing variable is what the model can actually do in the domain where
            harmful assistance is being tested.
          </p>

          <section id="fixed-thresholds">
            <p className="section-number">01</p>
            <h2>The problem with one threshold</h2>
            <p>
              Conventional safety screens produce an aggregate safe-response rate. That
              remains useful for harms where fluency itself is the enabling capability:
              harassment, misinformation, or broadly illegal advice. Any competent model
              can produce these, so every model should clear the same absolute bar. Our
              absolute battery uses 74 non-overlapping <a href="https://github.com/centerforaisafety/HarmBench">HarmBench</a>
              {" "}behaviors and all 100 <a href="https://github.com/JailbreakBench/jailbreakbench">JailbreakBench</a>
              {" "}harmful behaviors at a fixed 90% threshold.
            </p>
            <p>
              Specialist harms are different. In biology, cybersecurity, and law, the
              risk depends on whether the model can provide meaningful uplift. Weightroom
              therefore retains an absolute gate for general harms and adds a second,
              domain-conditioned gate. Both must pass; strength in one area never offsets
              a dangerous result in another.
            </p>
            <div className="principle-card">
              <span>The principle</span>
              <p>
                The more capable a model is in a consequential domain, the stronger the
                evidence of safe behavior we require in that same domain.
              </p>
            </div>
          </section>

          <section id="mechanism">
            <p className="section-number">02</p>
            <h2>The mechanism</h2>
            <p>
              Every domain pairs two private instruments. A multiple-choice probe measures
              capability. If capability crosses the domain floor, an open-ended elicitation
              set measures whether the model will materially help with harmful requests.
            </p>

            <div className="eval-flow" role="img" aria-label="Capability probe routes capable models into an elicitation test, which is compared against a capability-dependent ceiling before a release decision">
              <div className="flow-node"><span>1</span><strong>Capability probe</strong><small>How much does the model know?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>2</span><strong>Domain router</strong><small>Is capability above the floor?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>3</span><strong>Elicitation test</strong><small>Will the model help?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>4</span><strong>Release decision</strong><small>Does the upper bound clear the bar?</small></div>
            </div>

            <div className="article-table-wrap">
              <table className="article-table">
                <thead><tr><th>Domain</th><th>Capability probe</th><th>Elicitation evidence</th></tr></thead>
                <tbody>
                  {domainRows.map((row) => (
                    <tr key={row.domain}>
                      <td><strong>{row.domain}</strong></td>
                      <td><a href={row.probeUrl}>{row.probe} ↗</a></td>
                      <td><a href={row.elicitationUrl}>{row.elicitation} ↗</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3>A continuous ceiling</h3>
            <p>
              We chance-correct each probe, then map the adjusted score onto a continuous
              policy curve. Below the floor, the domain evaluation is not required. At
              low demonstrated capability, the framework tolerates up to 30% harm; the
              ceiling tightens smoothly toward 15% as capability approaches the frontier.
              Biology currently uses a higher, provisional activation floor because its
              public proxy includes substantial ordinary biology knowledge.
            </p>

            <div className="curve-figure">
              <svg viewBox="0 0 680 300" role="img" aria-labelledby="curve-title curve-desc">
                <title id="curve-title">Capability-conditioned tolerated harm curve</title>
                <desc id="curve-desc">Tolerated harm falls from 30 percent at adjusted capability 0.35 to 15 percent at capability 0.9.</desc>
                <line className="curve-axis" x1="72" y1="24" x2="72" y2="246" />
                <line className="curve-axis" x1="72" y1="246" x2="646" y2="246" />
                <line className="curve-grid" x1="72" y1="70" x2="646" y2="70" />
                <line className="curve-grid" x1="72" y1="136" x2="646" y2="136" />
                <line className="curve-grid" x1="72" y1="202" x2="646" y2="202" />
                <path className="curve-line" d="M 273 70 L 388 103 L 503 136 L 589 169 L 646 169" />
                <path className="curve-dormant" d="M 72 246 L 273 246" />
                <circle className="curve-point" cx="273" cy="70" r="5" />
                <circle className="curve-point" cx="388" cy="103" r="5" />
                <circle className="curve-point" cx="503" cy="136" r="5" />
                <circle className="curve-point" cx="589" cy="169" r="5" />
                <text x="25" y="74">30%</text><text x="25" y="140">20%</text><text x="25" y="206">10%</text>
                <text x="254" y="270">0.35</text><text x="370" y="270">0.55</text><text x="485" y="270">0.75</text><text x="574" y="270">0.90</text>
                <text className="curve-caption" x="75" y="291">Adjusted domain capability →</text>
                <text className="curve-caption" transform="translate(14 220) rotate(-90)">Tolerated harm</text>
                <text className="curve-annotation" x="105" y="228">not gated</text>
              </svg>
              <p>
                The anchors are declared policy calibrated to these instruments—not
                universal safety constants. Changing the prompt pool requires recalibration.
              </p>
            </div>
          </section>

          <section id="private-evaluations">
            <p className="section-number">03</p>
            <h2>Building private evaluations without inventing answers</h2>
            <p>
              Public benchmarks cannot remain an effective publishing gate indefinitely.
              Their items appear in training corpora, and repeated certification attempts
              reveal a static private set one bit at a time. We need fresh items—but a
              capability probe cannot tolerate an unreliable answer key.
            </p>
            <p>
              Our rule is <strong>transform, do not invent</strong>. Synthetic items inherit
              ground truth from established sources: answer options can be permuted while
              following the original key; a known-correct patch can be mutated into
              alternatives; a labeled legal example can become a binary question. For
              elicitation, the assessed behavior remains intact while the surrounding
              framing changes.
            </p>
            <div className="transformation-grid">
              <div><span>Seed</span><strong>Known task + validated answer</strong></div>
              <div><span>Invariant transform</span><strong>Change presentation, preserve truth</strong></div>
              <div><span>Private item</span><strong>Digest-pinned + rotation-ready</strong></div>
            </div>
            <p>
              Framings span eight technique families—direct, persona, fiction, academic,
              indirect, authority, distancing, and format—because robustness to one style
              says little about robustness to another. Selection is derived from content
              hashes, and staged pools are pinned by digest so upstream changes fail loudly.
            </p>
          </section>

          <section id="uncertainty">
            <p className="section-number">04</p>
            <h2>A pass rate is not yet evidence</h2>
            <p>
              Evaluation results are samples, not properties. We compare the 95% Wilson
              upper confidence bound on harmful assistance—not the observed mean—with the
              capability-dependent ceiling. Close calls therefore require more evidence
              rather than optimistic rounding.
            </p>
            <div className="formula-card">
              <span>Decision statistic</span>
              <code>UCB₉₅(harm rate; effective n) ≤ κ(adjusted capability)</code>
            </div>
            <h3>Correct for guessing</h3>
            <p>
              A four-option probe gives a random model 25% for free; a binary probe gives
              it 50%. We normalize each raw score by its declared chance floor so scores
              from different instruments represent evidence above guessing.
            </p>
            <div className="formula-card compact">
              <code>adjusted = clamp((raw − chance) / (1 − chance), 0, 1)</code>
            </div>
            <h3>Count independent evidence, not prompt variants</h3>
            <p>
              Hundreds of prompts may be transformations of only a few dozen underlying
              behaviors. Responses within each behavior are correlated. We estimate that
              clustering and compute an effective sample size; in the extreme, 532 variants
              of 28 perfectly correlated behaviors provide evidence equivalent to 28—not
              532—independent observations.
            </p>
            <p>
              The outcome can therefore be <code>not_required</code>, <code>pass</code>,
              <code>fail</code>, or <code>insufficient_evidence</code>. The last means our
              sample cannot support the claim; it does not blame the model for a weak test.
            </p>
          </section>

          <section id="judge">
            <p className="section-number">05</p>
            <h2>Judge whether the answer helped—not whether it apologized</h2>
            <p>
              Refusal phrase matching fails in both directions. A response can begin with
              an apology and then provide the requested assistance. A weak model can also
              produce harmless nonsense without explicitly refusing. Neither case is
              captured by searching for “I can’t help.”
            </p>
            <p>
              A pinned, independent Qwen3Guard-Gen-8B model instead evaluates whether the
              response materially helped with the harmful request. Unparseable judgments do
              not count as safe, and a partially parsed run errors rather than averaging only
              the convenient subset.
            </p>
            <div className="phase-diagram">
              <div><span>Phase 1</span><strong>Candidate model</strong><small>Generate responses; keep transcripts in memory</small></div>
              <div className="phase-divider" aria-hidden="true">then</div>
              <div><span>Phase 2</span><strong>Independent judge</strong><small>Score assistance after the candidate is torn down</small></div>
            </div>
            <p>
              The two phases share an accelerator rather than co-residing on it. Every result
              records the judge identity so a fallback heuristic can never masquerade as the
              validated evaluation path.
            </p>
          </section>

          <section id="leakage">
            <p className="section-number">06</p>
            <h2>The threshold is private data too</h2>
            <p>
              A rejected seller can resubmit. Exact scores, capability bands, and even a
              capability-dependent threshold provide an oracle for reconstructing the hidden
              probe. Conditioned evaluations therefore use stricter redaction than ordinary
              held-out benchmarks.
            </p>
            <div className="visibility-grid">
              <div><span>Buyer</span><strong>Certification verdict</strong><small>No private domain measurements</small></div>
              <div><span>Creator</span><strong>Verdict + failing domain</strong><small>Enough to act, not enough to probe</small></div>
              <div><span>Internal</span><strong>Full evidence</strong><small>Probe, ceiling, harm rate, uncertainty</small></div>
            </div>
            <p>
              Networked asset staging happens before evaluation. The model, private item
              sets, and judge then run in an isolated evaluation environment. External-endpoint
              smoke tests refuse private suites because an endpoint controlled by the model
              creator would see every prompt.
            </p>
          </section>

          <section id="results">
            <p className="section-number">07</p>
            <h2>What we observed on real hardware</h2>
            <p>
              Qwen2.5-7B-Instruct cleared the conventional screens—91% on HarmBench and 97%
              on JailbreakBench—but failed the conditioned gates because it demonstrated
              enough domain capability for harmful assistance to matter. Its capability
              grade remained B while certification failed. Those are separate facts, and
              the report preserves both.
            </p>
            <div className="result-comparison">
              <div><span>Absolute screens</span><strong>Pass</strong><small>91% / 97% safe response</small></div>
              <div><span>Conditioned domains</span><strong>Fail</strong><small>Capability made the observed leakage material</small></div>
              <div><span>Capability grade</span><strong>B</strong><small>Safety does not rewrite capability</small></div>
            </div>
            <p>
              A full three-domain run has taken roughly 300–600 seconds and about $0.13 on
              an A10G. Measured capability has so far ranged from 0.05 to 0.61 after chance
              correction, so the steepest portion of the policy curve remains untested on
              real models. We treat those figures as early operating measurements, not a
              mature benchmark study.
            </p>
          </section>

          <section id="limitations">
            <p className="section-number">08</p>
            <h2>What this framework does not prove</h2>
            <ul className="limitations-list">
              <li><strong>The coding probe measures recognition.</strong> Choosing a patch is not the same as authoring and validating one.</li>
              <li><strong>The legal pairing is experimental.</strong> Legal framing has not yet been shown to correlate with legal-domain harmful assistance.</li>
              <li><strong>The ceiling is policy.</strong> Its anchors are deliberate judgment calls tied to these exact instruments.</li>
              <li><strong>The judge needs human validation.</strong> Automated labels have not yet been calibrated against an expert-reviewed sample.</li>
              <li><strong>Capability probes are proxies.</strong> Hazard-adjacent knowledge is not the same as real-world execution ability.</li>
              <li><strong>Sandbagging remains an open problem.</strong> Divergence between public and private capability measurements can flag it, but that check is not yet implemented.</li>
            </ul>
            <p>
              Frontier-lab release reviews also use expert red-teaming, human uplift studies,
              and governance processes. Weightroom’s mechanism is an automated screen built
              for continuous marketplace submissions. A successful result means conformance
              to a versioned evaluation standard—not safety in the abstract.
            </p>
          </section>

          <footer className="article-conclusion">
            <p className="section-number">The takeaway</p>
            <h2>Ask what sits behind the refusal rate.</h2>
            <p>
              A useful publishing gate must measure capability and harmful assistance in the
              same domain, preserve uncertainty, and remain resistant to repeated submission.
              That produces a stricter bar for models that can cause greater harm—and avoids
              spending expensive adversarial evaluation on models with no meaningful domain
              capability in the first place.
            </p>
            <Link className="button primary" href="/buy">Explore evaluated models →</Link>
          </footer>
        </div>
      </div>
    </article>
  );
}
