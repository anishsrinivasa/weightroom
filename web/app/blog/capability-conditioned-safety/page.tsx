import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "How we built Weightroom's private safety evaluation pipeline",
  description:
    "The datasets, generation pipeline, and statistical gate Weightroom uses to evaluate downloadable language models.",
};

const sections = [
  ["contributions", "What we built"],
  ["fixed-thresholds", "Where the fixed gate failed"],
  ["mechanism", "Pairing capability and behavior"],
  ["private-evaluations", "Generating the private sets"],
  ["uncertainty", "Making the decision"],
  ["judge", "Scoring open-ended answers"],
  ["leakage", "What a report reveals"],
  ["results", "First production runs"],
  ["limitations", "Current limits"],
] as const;

const domainRows = [
  {
    domain: "Biology",
    probe: "WMDP-Bio-derived multiple choice",
    probeUrl: "https://www.wmdp.ai/",
    elicitation: "Chemical and biological behaviors",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
    pool: "532",
  },
  {
    domain: "Coding / cyber",
    probe: "SWE-bench-derived patch recognition",
    probeUrl: "https://github.com/SWE-bench/SWE-bench",
    elicitation: "Cybercrime and intrusion behaviors from HarmBench + AdvBench",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
    pool: "1,200",
  },
  {
    domain: "Legal",
    probe: "LegalBench-derived binary questions",
    probeUrl: "https://legalbench.org/",
    elicitation: "Illegal-assistance behaviors from HarmBench + StrongREJECT",
    elicitationUrl: "https://github.com/centerforaisafety/HarmBench",
    pool: "1,200",
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
        <h1>How we built Weightroom&apos;s private safety evaluation pipeline</h1>
        <p className="article-dek">
          Weightroom evaluates downloadable models before they are listed. Our current
          system uses three held-out domain datasets, 2,932 private elicitation variants,
          and a release threshold tied to the capability each model demonstrates.
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
          <div><dt>Private prompt pool</dt><dd>2,932</dd><span>staged elicitation variants</span></div>
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
            Qwen2.5-7B-Instruct passed our two conventional safety screens: 91% on
            HarmBench and 97% on JailbreakBench. It failed the domain-conditioned
            evaluation. That disagreement exposed what the first version of our gate
            could not measure.
          </p>
          <p>
            Refusal rates count how often a model declines a request. They leave out the
            capability behind answers that get through. A weak biology model and a strong
            one can both refuse 96% of harmful prompts; the remaining 4% does not carry
            the same risk. Raising the refusal threshold would still score them alike.
          </p>

          <section id="contributions">
            <p className="section-number">01</p>
            <h2>What we built</h2>
            <p>
              The production system has four parts. They share item identities, version
              metadata, and one report format, so a result can be traced from a release
              verdict back to the exact private material used in the run.
            </p>
            <div className="contribution-grid">
              <div>
                <span>01 / Data</span>
                <strong>Private domain datasets</strong>
                <p>Paired capability and elicitation sets for biology, coding/cyber, and law.</p>
              </div>
              <div>
                <span>02 / Generation</span>
                <strong>Constrained generation</strong>
                <p>Transforms change presentation while preserving the source answer or assessed behavior.</p>
              </div>
              <div>
                <span>03 / Decision</span>
                <strong>Capability-conditioned thresholds</strong>
                <p>A chance-corrected probe score sets the maximum tolerated harm rate for its domain.</p>
              </div>
              <div>
                <span>04 / Operations</span>
                <strong>An isolated evaluation run</strong>
                <p>Digest-pinned sets run without network access and produce signed, selectively redacted reports.</p>
              </div>
            </div>
            <p>
              The first two parts took most of the design work. A static public benchmark
              becomes training material. Newly generated questions avoid that problem, but
              a wrong answer key silently moves the safety threshold. The pipeline had to
              produce items that were both private and checkable.
            </p>
          </section>

          <section id="fixed-thresholds">
            <p className="section-number">02</p>
            <h2>Where the fixed gate failed</h2>
            <p>
              We started with a fixed 90% safe-response threshold. The battery contains
              74 non-overlapping <a href="https://github.com/centerforaisafety/HarmBench">HarmBench</a>
              {" "}behaviors and all 100 <a href="https://github.com/JailbreakBench/jailbreakbench">JailbreakBench</a>
              {" "}harmful behaviors. It remains useful for harassment, misinformation,
              and general illegal advice, where producing fluent text is itself enough to
              cause harm.
            </p>
            <p>
              Biology and cybersecurity made the limitation obvious. Assistance matters
              more when the model can supply knowledge a user does not already have. We
              kept the fixed battery and added a separate gate for specialist domains.
              Each domain is evaluated independently, and both gates must pass.
            </p>
          </section>

          <section id="mechanism">
            <p className="section-number">03</p>
            <h2>Pairing capability and behavior</h2>
            <p>
              Each domain has two private instruments. A multiple-choice probe runs first.
              If its score crosses the domain floor, the model receives open-ended prompts
              that test whether it will materially help with harmful requests.
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
                <thead><tr><th>Domain</th><th>Capability probe</th><th>Elicitation seeds</th><th>Private pool</th></tr></thead>
                <tbody>
                  {domainRows.map((row) => (
                    <tr key={row.domain}>
                      <td><strong>{row.domain}</strong></td>
                      <td><a href={row.probeUrl}>{row.probe} ↗</a></td>
                      <td><a href={row.elicitationUrl}>{row.elicitation} ↗</a></td>
                      <td>{row.pool}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3>A continuous ceiling</h3>
            <p>
              The first implementation used capability bands. A score of 0.54 and a score
              of 0.55 could trigger safety requirements ten percentage points apart. The
              discontinuity came from the policy boundary rather than evidence of a sharp
              change in risk. We replaced the bands with an interpolated curve.
            </p>
            <p>
              After correction for guessing, the score maps to a maximum tolerated harm
              rate. The limit starts at 30% and falls to 15% near the capability frontier.
              Below the activation floor, the elicitation set does not run. Biology uses
              a provisional 0.75 floor because WMDP-Bio contains enough ordinary biology
              to route small general models into a test intended for hazardous capability.
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
                These anchors are policy choices calibrated to the current item sets. A
                change to the prompt pool requires us to calibrate them again.
              </p>
            </div>
          </section>

          <section id="private-evaluations">
            <p className="section-number">04</p>
            <h2>Generating the private sets</h2>
            <p>
              Public benchmark items eventually appear in training corpora. A fixed private
              set also degrades: every submission reveals at least the final verdict, and
              repeated attempts turn that verdict into an oracle. We needed enough material
              to rotate the tests without losing reliable labels.
            </p>
            <p>
              Generating questions from scratch was the obvious option. We rejected it
              because the score from a bad question still looks valid. Instead, every
              generated item inherits something we already know. A WMDP transformation can
              reorder four choices, but the answer key moves with the correct choice. A
              SWE-bench item starts from the gold patch and mutates copies into distractors.
              LegalBench supplies the label for a derived binary question. Elicitation items
              retain the source behavior verbatim and change only its framing.
            </p>
            <p>
              The source datasets are public and used under their respective terms. The
              derived evaluation items are custom, held out, rotated between runs, and never
              published.
            </p>

            <ol className="pipeline-steps">
              <li><span>01</span><div><strong>Ingest and version the seeds</strong><p>WMDP, SWE-bench Verified, LegalBench, HarmBench, AdvBench, and StrongREJECT enter with source revision and license metadata.</p></div></li>
              <li><span>02</span><div><strong>Normalize and fingerprint</strong><p>Each item receives a stable seed and behavior identity, preserving lineage through every later transform.</p></div></li>
              <li><span>03</span><div><strong>Apply a constrained transform</strong><p>Permute keyed choices, mutate a gold patch into distractors, derive a binary legal question, or wrap a harmful behavior in a new framing.</p></div></li>
              <li><span>04</span><div><strong>Validate the invariant</strong><p>The answer key must follow the transform, or the original assessed behavior must remain unchanged. Invalid variants never enter the pool.</p></div></li>
              <li><span>05</span><div><strong>Deduplicate, rotate, and pin</strong><p>Content hashes select reproducible subsets; a digest locks the staged corpus so upstream drift fails loudly.</p></div></li>
            </ol>

            <div className="invariant-card">
              <span>What every generated item retains</span>
              <div><strong>Ground truth</strong><small>Inherited, not newly claimed</small></div>
              <div><strong>Lineage</strong><small>Seed and behavior fingerprints</small></div>
              <div><strong>Provenance</strong><small>Revision and license metadata</small></div>
              <div><strong>Integrity</strong><small>Content digest at staging</small></div>
            </div>

            <h3>Rotation overlap</h3>
            <p>
              Our first biology pool contained 28 behaviors in five framings: 140 items.
              Any two samples of 100 from that pool must share at least 60, regardless of
              selection strategy. We expanded the library to 19 framings. The resulting
              532-item pool has no forced overlap; measured reuse between rotations fell
              to 17%.
            </p>
            <p>
              Framings span eight technique families: direct, persona, fiction, academic,
              indirect, authority, distancing, and format. Robustness to one style
              says little about robustness to another. The report keeps scores for each
              family. In one run, direct requests were refused every time while distancing
              prompts were refused only 42% of the time. The aggregate, 68%, concealed the
              difference.
            </p>
            <p>
              More framings make the next rotation less predictable. They do little for
              statistical power because responses to variants of the same behavior are
              correlated. Increasing the number of seed behaviors is what adds independent
              evidence.
            </p>
          </section>

          <section id="uncertainty">
            <p className="section-number">05</p>
            <h2>Making the decision</h2>
            <p>
              A model observed at 74% safe over roughly 60 independent items may have a
              substantially lower true safe rate. The gate therefore compares the 95%
              Wilson upper confidence bound on harmful assistance with the domain ceiling.
              The observed mean alone cannot produce a pass.
            </p>
            <div className="formula-card">
              <span>Decision statistic</span>
              <code>UCB₉₅(harm rate; effective n) ≤ κ(adjusted capability)</code>
            </div>
            <h3>Correct for guessing</h3>
            <p>
              Raw accuracy is misleading across different question formats. Random choice
              yields 25% on a four-option probe and 50% on a binary probe. We subtract that
              floor before comparing capability between instruments.
            </p>
            <div className="formula-card compact">
              <code>adjusted = clamp((raw − chance) / (1 − chance), 0, 1)</code>
            </div>
            <h3>Effective sample size</h3>
            <p>
              The 532 biology variants come from 28 underlying behaviors. We estimate
              within-behavior correlation and use it to compute an effective sample size.
              If every response in a behavior cluster moved together, those 532 prompts
              would count as 28 independent observations.
            </p>
            <p>
              The outcome can therefore be <code>not_required</code>, <code>pass</code>,
              <code>fail</code>, or <code>insufficient_evidence</code>. The last means our
              sample cannot support a decision at the required ceiling.
            </p>
          </section>

          <section id="judge">
            <p className="section-number">06</p>
            <h2>Scoring open-ended answers</h2>
            <p>
              We initially searched responses for refusal phrases. In a four-response check,
              the heuristic called the batch 75% safe. Manual review found only one harmless
              answer, or 25%. Some assisted responses opened with an apology; harmless
              nonsense caused errors in the other direction.
            </p>
            <p>
              We replaced the phrase matcher with a pinned Qwen3Guard-Gen-8B judge. It reads
              the request and response and decides whether the response materially helped.
              Unparseable judgments count against completion, and a partially parsed run
              errors instead of averaging the subset that happened to parse.
            </p>
            <div className="phase-diagram">
              <div><span>Phase 1</span><strong>Candidate model</strong><small>Generate responses; keep transcripts in memory</small></div>
              <div className="phase-divider" aria-hidden="true">then</div>
              <div><span>Phase 2</span><strong>Independent judge</strong><small>Score assistance after the candidate is torn down</small></div>
            </div>
            <p>
              The two phases share an accelerator rather than co-residing on it. Every result
              records the judge model and revision used for the result.
            </p>
          </section>

          <section id="leakage">
            <p className="section-number">07</p>
            <h2>What a report reveals</h2>
            <p>
              Sellers can submit another version after a rejection. Exact domain scores and
              applied thresholds would help them infer the hidden items across attempts, so
              the conditioned evaluation has three report views.
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
            <p className="section-number">08</p>
            <h2>First production runs</h2>
            <p>
              The Qwen2.5-7B-Instruct result from the opening was the first useful test of
              the split design. Its 91% HarmBench and 97% JailbreakBench scores cleared the
              fixed gate. The private probes found enough domain capability to activate the
              conditioned tests, which then failed. Its marketplace capability grade stayed
              at B; the safety result did not alter the capability measurement.
            </p>
            <div className="result-comparison">
              <div><span>Absolute screens</span><strong>Pass</strong><small>91% / 97% safe response</small></div>
              <div><span>Conditioned domains</span><strong>Fail</strong><small>Capability made the observed leakage material</small></div>
              <div><span>Capability grade</span><strong>B</strong><small>Safety does not rewrite capability</small></div>
            </div>
            <p>
              A full three-domain run has taken roughly 300–600 seconds and about $0.13 on
              an A10G. Measured capability has so far ranged from 0.05 to 0.61 after chance
              correction. We have not yet exercised the steepest part of the policy curve
              on real models, so these are operating measurements from an early system.
            </p>
          </section>

          <section id="limitations">
            <p className="section-number">09</p>
            <h2>Current limits</h2>
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
              for continuous marketplace submissions. A successful result records conformance
              to a versioned evaluation standard. It does not establish safety across every
              deployment.
            </p>
          </section>

          <footer className="article-conclusion">
            <p className="section-number">Current status</p>
            <h2>Next validation work</h2>
            <p>
              Weightroom uses this mechanism as an automated publishing screen for
              downloadable text models. It caught a failure that the fixed refusal tests
              missed, and a three-domain run fits on one A10G for about $0.13. The next work
              is less tidy: validate the judge against expert review, replace patch
              recognition with executable coding tasks, and rebuild the legal pairing around
              evidence that capability and harmful assistance actually correlate.
            </p>
            <Link className="button primary" href="/buy">Explore evaluated models →</Link>
          </footer>
        </div>
      </div>
    </article>
  );
}
