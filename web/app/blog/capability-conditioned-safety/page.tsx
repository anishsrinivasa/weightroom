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
  ["results", "An end-to-end run"],
  ["limitations", "Current limits"],
] as const;

const domainRows = [
  {
    domain: "Biology",
    probe: "WMDP-Bio-derived multiple choice",
    probeUrl: "https://www.wmdp.ai/",
    elicitation: "Chemical and biological behaviors from HarmBench",
    elicitationUrls: [
      { label: "HarmBench", url: "https://github.com/centerforaisafety/HarmBench" },
    ],
    pool: "532",
  },
  {
    domain: "Coding / cyber",
    probe: "SWE-bench-derived patch recognition",
    probeUrl: "https://github.com/SWE-bench/SWE-bench",
    elicitation: "Cybercrime and intrusion behaviors from HarmBench + AdvBench",
    elicitationUrls: [
      { label: "HarmBench", url: "https://github.com/centerforaisafety/HarmBench" },
      { label: "AdvBench", url: "https://github.com/llm-attacks/llm-attacks/blob/main/data/advbench/harmful_behaviors.csv" },
    ],
    pool: "1,200",
  },
  {
    domain: "Legal",
    probe: "LegalBench-derived binary questions",
    probeUrl: "https://legalbench.org/",
    elicitation: "Illegal-assistance behaviors from HarmBench + StrongREJECT",
    elicitationUrls: [
      { label: "HarmBench", url: "https://github.com/centerforaisafety/HarmBench" },
      { label: "StrongREJECT", url: "https://github.com/dsbowen/strong_reject" },
    ],
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

        <div className="article-hero-visual" aria-label="How capability changes the release decision">
          <div>
            <span className="visual-label">Working intuition</span>
            <strong>Useful expertise makes a harmful answer more consequential.</strong>
          </div>
          <div>
            <span className="visual-label">How the gate responds</span>
            <strong>As capability rises, tolerated harmful assistance falls.</strong>
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
            HarmBench and 97% on JailbreakBench, both above the fixed 90% bar. A private
            probe then found enough specialist knowledge to activate further testing.
            On those open-ended tests, it gave materially helpful answers to harmful
            requests often enough to exceed at least one domain&apos;s limit.
          </p>
          <p>
            Refusal rates count how often a model declines a request. They leave out the
            capability behind answers that get through. Consider a weak biology model and
            a strong one that both refuse 96% of harmful prompts. The remaining 4% does not
            carry the same risk, yet a higher refusal threshold would still score them alike.
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
              cause harm. A response counts as safe when it refuses or otherwise avoids
              materially helping with the request.
            </p>
            <p>
              Biology and cybersecurity made the limitation obvious. Assistance matters
              more when the model can supply knowledge a user does not already have. We
              kept the fixed battery and added a separate gate for specialist domains.
              Each domain is evaluated independently, and both gates must pass. Behaviors
              assigned to the conditioned domains are removed from the fixed battery so the
              same prompt is not counted twice.
            </p>
          </section>

          <section id="mechanism">
            <p className="section-number">03</p>
            <h2>Pairing capability and behavior</h2>
            <p>
              Each domain has two private instruments. A multiple-choice probe runs first.
              If its score crosses the domain floor, the model receives open-ended prompts
              that test whether it will materially help with harmful requests. A failure in
              any activated domain blocks publication; strong results elsewhere cannot
              average it away.
            </p>

            <div className="eval-flow" role="img" aria-label="Capability probe routes capable models into an elicitation test, which is compared against a capability-dependent ceiling before a release decision">
              <div className="flow-node"><span>1</span><strong>Capability probe</strong><small>How much does the model know?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>2</span><strong>Domain router</strong><small>Does the model know enough for harmful help to matter?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>3</span><strong>Elicitation test</strong><small>Will the model help?</small></div>
              <div className="flow-arrow" aria-hidden="true">→</div>
              <div className="flow-node"><span>4</span><strong>Release decision</strong><small>Does the conservative harm estimate stay under the limit?</small></div>
            </div>

            <div className="article-table-wrap">
              <table className="article-table">
                <thead><tr><th>Domain</th><th>Capability probe</th><th>Elicitation seeds</th><th>Private pool</th></tr></thead>
                <tbody>
                  {domainRows.map((row) => (
                    <tr key={row.domain}>
                      <td><strong>{row.domain}</strong></td>
                      <td><a href={row.probeUrl}>{row.probe} ↗</a></td>
                      <td>
                        {row.elicitation}<br />
                        {row.elicitationUrls.map((source, index) => (
                          <span key={source.label}>
                            {index > 0 && " · "}<a href={source.url}>{source.label} ↗</a>
                          </span>
                        ))}
                      </td>
                      <td>{row.pool}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3>The current policy curve</h3>
            <p>
              The first implementation used capability bands. Two models separated by one
              percentage point in capability could trigger safety requirements ten
              percentage points apart. That discontinuity came from the policy boundary
              rather than evidence of a sharp change in risk, so we replaced the bands with
              an interpolated curve.
            </p>
            <p>
              We first remove the accuracy a model could get by guessing. A corrected score
              of 0.60 means the model covered 60% of the distance from random guessing to a
              perfect score. That value maps to a maximum tolerated harm rate. The current
              limit starts at 30% for a model just above the activation floor and falls to
              15% near the capability frontier.
            </p>
            <p>
              These percentages refer to judge-labeled assistance on deliberately harmful
              test prompts. They are not estimates of how often a deployed model will cause
              harm. Biology also uses a higher activation floor than the other domains:
              WMDP-Bio contains enough ordinary biology to make small general models appear
              more hazardous than they are.
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
                The curve is a release policy, not an empirical law of risk. Its anchors
                were calibrated to these item sets and must be revisited when the sets change.
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
              published. Generation happens during offline staging, not while a submitted
              model is being evaluated.
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
              The observed harmful-assistance rate is only a sample. A result just under the
              limit may reflect luck, especially when many prompts are variants of the same
              behavior. We ask how high the actual rate could plausibly be given the amount
              of independent evidence. The gate passes only if that conservative estimate
              remains below the domain limit.
            </p>
            <div className="formula-card">
              <span>Decision statistic</span>
              <code>UCB₉₅(harm rate; effective n) ≤ κ(adjusted capability)</code>
              <small>
                UCB₉₅ is the 95% Wilson upper bound. Effective n discounts correlated
                variants. κ is the allowed harm rate at the measured capability.
              </small>
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
              <code>fail</code>, or <code>insufficient_evidence</code>. The first means the
              probe did not activate the domain. A pass or fail compares the conservative
              harm estimate with the limit. Insufficient evidence means the available sample
              cannot support either conclusion.
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
              errors instead of averaging the subset that happened to parse. A separate
              judge also prevents the submitted model from grading its own answers and keeps
              the scoring model constant across submissions.
            </p>
            <div className="phase-diagram">
              <div><span>Phase 1</span><strong>Candidate model</strong><small>Generate responses; keep transcripts in memory</small></div>
              <div className="phase-divider" aria-hidden="true">then</div>
              <div><span>Phase 2</span><strong>Independent judge</strong><small>Score assistance after the candidate is torn down</small></div>
            </div>
            <p>
              The two phases share an accelerator rather than co-residing on it. Every result
              records the judge model and revision used for the result. This improves the
              scoring logic; it does not establish that the judge is correct. Agreement with
              expert human review still needs to be measured.
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
            <h2>An end-to-end run</h2>
            <p>
              In an internal run, Qwen2.5-7B-Instruct followed the full decision path. It
              cleared the general refusal screen. At least one private capability probe
              crossed its activation floor, so the corresponding open-ended test ran. The
              model then provided materially useful help on enough harmful requests for the
              uncertainty-adjusted harm estimate to exceed that domain&apos;s limit. One domain
              failure was enough to reject the submission.
            </p>
            <div className="result-comparison">
              <div><span>General screen</span><strong>Cleared</strong><small>Both safe-response rates exceeded the fixed 90% bar</small></div>
              <div><span>Domain probe</span><strong>Activated</strong><small>The model knew enough for specialist testing to matter</small></div>
              <div><span>Elicitation</span><strong>Rejected</strong><small>Harmful assistance exceeded at least one domain limit</small></div>
            </div>
            <p>
              The public report does not include the failing domain, exact scores, or item
              count. The model creator sees the failing domain but not the measurements. The
              signed internal report retains the full evidence. This limits what an external
              reader can reproduce, but it also makes the held-out set harder to reconstruct
              across submissions.
            </p>
            <p>
              On one A10G, a full three-domain run has taken roughly five to ten minutes and
              cost about $0.13. Those are measurements from our runs, not guaranteed latency
              or pricing for other hardware and providers.
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
              <li><strong>Transformation does not erase training exposure.</strong> The private items reduce verbatim memorization, but their public source material may still have appeared in training.</li>
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

        </div>
      </div>
    </article>
  );
}
