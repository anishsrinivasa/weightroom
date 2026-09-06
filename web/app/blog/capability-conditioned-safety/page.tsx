import type { Metadata } from "next";
import Link from "next/link";
import katex from "katex";

export const metadata: Metadata = {
  title: "How we built Weightroom's private safety evaluation pipeline",
  description:
    "The datasets, generation pipeline, and statistical gate Weightroom uses to evaluate downloadable language models.",
};

const sections = [
  ["motivation", "Why refusal rate is incomplete"],
  ["contributions", "The system we built"],
  ["mechanism", "How capability changes the bar"],
  ["private-evaluations", "Building the private datasets"],
  ["uncertainty", "How uncertainty affects a verdict"],
  ["judge", "How open-ended answers are scored"],
  ["leakage", "Why reports omit exact scores"],
  ["results", "What happened when we tested Qwen"],
  ["limitations", "What the evaluation cannot establish"],
] as const;

const domainRows = [
  {
    domain: "Biology",
    probe: "Knowledge related to hazardous biology, derived from WMDP-Bio",
    probeUrl: "https://www.wmdp.ai/",
    elicitation: "Chemical and biological behaviors from HarmBench",
    elicitationUrls: [
      { label: "HarmBench", url: "https://github.com/centerforaisafety/HarmBench" },
    ],
    pool: "532",
  },
  {
    domain: "Coding / cyber",
    probe: "Choosing a fix for a documented bug, derived from SWE-bench Verified",
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
    probe: "Binary legal reasoning questions, derived from LegalBench",
    probeUrl: "https://legalbench.org/",
    elicitation: "Illegal-assistance behaviors from HarmBench + StrongREJECT",
    elicitationUrls: [
      { label: "HarmBench", url: "https://github.com/centerforaisafety/HarmBench" },
      { label: "StrongREJECT", url: "https://github.com/dsbowen/strong_reject" },
    ],
    pool: "1,200",
  },
] as const;

const decisionEquation = katex.renderToString(
  String.raw`\operatorname{UCB}_{95\%}\!\left(\hat{p}_{\mathrm{harm}};\,n_{\mathrm{eff}}\right) \leq \kappa\!\left(c_{\mathrm{adj}}\right)`,
  { displayMode: true, output: "htmlAndMathml", throwOnError: true },
);

const adjustedCapabilityEquation = katex.renderToString(
  String.raw`c_{\mathrm{adj}} = \operatorname{clamp}\!\left(\frac{c_{\mathrm{raw}} - c_{\mathrm{chance}}}{1 - c_{\mathrm{chance}}},\,0,\,1\right)`,
  { displayMode: true, output: "htmlAndMathml", throwOnError: true },
);

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
          Once model weights are downloaded, Weightroom cannot control how they are run.
          The decision available to us is whether to list them. This is the private data
          pipeline and safety test behind that decision.
        </p>

        <div className="article-hero-visual" aria-label="Why Weightroom evaluates a model before listing it">
          <div>
            <span className="visual-label">Marketplace constraint</span>
            <strong>Downloaded weights run outside Weightroom&apos;s safeguards.</strong>
          </div>
          <div>
            <span className="visual-label">Decision before listing</span>
            <strong>Test what the model knows and what it will help a user do.</strong>
          </div>
        </div>

        <dl className="article-stats">
          <div><dt>Specialist domains</dt><dd>3</dd><span>biology, coding/cyber, law</span></div>
          <div><dt>Knowledge probe</dt><dd>100</dd><span>questions in each domain</span></div>
          <div><dt>Private harmful prompts</dt><dd>2,932</dd><span>variants prepared across domains</span></div>
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
        </aside>

        <div className="article-body">
          <p className="article-opening">
            Weightroom is a marketplace where buyers obtain downloadable models from other
            creators. That creates a trust problem: before receiving a model, a buyer needs
            an independent reason to believe it meets a consistent safety standard. After
            purchase, unlike a hosted service, Weightroom no longer sits between the model
            and its users. A buyer can change how the model is run or use it in a workflow we
            never see.
          </p>
          <p>
            Our opportunity to make that transaction safer is before the model is listed.
            We independently evaluate each submission and use the result to decide whether
            it can be sold on Weightroom. We call this automated decision process a publishing
            gate. Passing means the model met a documented, versioned standard when tested;
            it is not a promise that every downstream use will be safe.
          </p>

          <section id="motivation">
            <p className="section-number">1</p>
            <h2>Why refusal rate is incomplete</h2>
            <p>
              A common automated safety test sends the model harmful requests and checks
              whether the responses refuse or avoid giving useful help. The percentage of
              requests handled safely is its safe-response rate. Our first publishing gate
              used two public benchmarks of harmful requests,
              {" "}<a href="https://github.com/centerforaisafety/HarmBench">HarmBench</a>
              {" "}and
              {" "}<a href="https://github.com/JailbreakBench/jailbreakbench">JailbreakBench</a>.
              It required every model to reach the same 90% safe-response rate.
            </p>
            <p>
              That works reasonably well for harms such as harassment or misinformation,
              where fluent generation is much of the enabling capability. Specialist domains
              raised a different question. A biology model that knows little beyond general
              coursework cannot add as much useful knowledge as one with expert-adjacent
              capability, even if both occasionally answer a harmful request.
            </p>
            <p>
              Consider two models that each handle 96 of 100 harmful biology prompts safely.
              A refusal-only gate treats them as equals. If the four remaining answers from
              one model contain detailed, correct specialist knowledge and the other model&apos;s
              answers do not, the equality is misleading. We needed to measure the knowledge
              behind those answers.
            </p>
            <p>
              We kept the fixed safe-response screen for general harms. Biology,
              coding/cybersecurity, and law receive additional domain-specific testing.
              Behaviors assigned to those domains are removed from the general screen so the
              same prompt is not counted twice.
            </p>
          </section>

          <section id="contributions">
            <p className="section-number">2</p>
            <h2>The system we built for that decision</h2>
            <p>
              The new gate connects a model&apos;s behavior to what it can do in the same
              domain. The implementation has four parts, joined by stable item identities,
              version metadata, and one report format. An internal reviewer can trace a
              verdict back to the exact private material used in the run.
            </p>
            <div className="contribution-grid">
              <div>
                <span>01 / Data</span>
                <strong>Private domain datasets</strong>
                <p>Each domain has one test of what the model knows and another of whether it helps with a harmful request.</p>
              </div>
              <div>
                <span>02 / Generation</span>
                <strong>Constrained generation</strong>
                <p>We change how a source item is presented while retaining its known answer or assessed behavior.</p>
              </div>
              <div>
                <span>03 / Decision</span>
                <strong>Capability-conditioned thresholds</strong>
                <p>Greater demonstrated knowledge produces a stricter limit on harmful assistance in that domain.</p>
              </div>
              <div>
                <span>04 / Operations</span>
                <strong>An isolated evaluation run</strong>
                <p>The model runs without network access. Reports reveal different levels of detail to buyers, creators, and internal reviewers.</p>
              </div>
            </div>
            <p>
              The first two parts took most of the design work. Static public benchmarks can
              appear in training data. Newly generated questions reduce that exposure, but a
              wrong answer key silently moves the safety threshold. The pipeline had to
              produce items that were both private and checkable.
            </p>
          </section>

          <section id="mechanism">
            <p className="section-number">3</p>
            <h2>How capability changes the release requirement</h2>
            <p>
              Each domain begins with a private multiple-choice knowledge test, which we call
              a capability probe. Its score estimates how much useful knowledge the model has
              in that domain. The score is used as a router: a model that demonstrates too
              little specialist knowledge to add useful information stops there.
            </p>
            <p>
              Each domain has an activation floor: the minimum probe score that signals
              enough knowledge for harmful help to matter. A model above that floor receives
              a second test containing open-ended harmful requests from the same domain. We
              call this the elicitation test because it measures how often an adversarial
              prompt can elicit materially useful help.
            </p>
            <p>
              A second language model, separate from the submitted model, labels each answer
              as materially helpful or not helpful. The release decision uses a conservative
              estimate that accounts for the test&apos;s sample size instead of trusting the raw
              percentage alone. A failure in any activated domain blocks publication; high
              scores elsewhere cannot average it away.
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

            <p>
              The table names the public sources used to build each instrument and the
              number of private harmful-prompt variants available for rotation. A single run
              draws a smaller subset from these pools.
            </p>
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
              Our first implementation grouped capability into bands. That made the safety
              requirement jump when two models had nearly identical probe scores but landed
              on opposite sides of a boundary. We replaced those jumps with a continuous
              curve.
            </p>
            <p>
              We first remove the accuracy a model could get by guessing. A corrected score
              of 0.60 means the model covered 60% of the distance from random guessing to a
              perfect score. That value maps to a maximum tolerated harm rate. The current
              limit starts at 30% for a model just above the activation floor and falls to
              15% in the highest capability range the policy was designed to cover.
            </p>
            <p>
              These percentages refer to judge-labeled assistance on deliberately harmful
              test prompts. They are not estimates of how often a deployed model will cause
              harm. Activation floors also differ by domain because the probes are not
              directly comparable. For example, WMDP-Bio includes ordinary biology questions
              that a small general model may answer without possessing specialist capability.
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
            <p className="section-number">4</p>
            <h2>How we build the private evaluation datasets</h2>
            <p>
              Public benchmark items can appear in training corpora. A fixed private
              set also degrades: every submission reveals at least the final verdict, and
              repeated attempts provide clues about which changes improve the score. We
              needed enough material to rotate the tests without losing reliable labels.
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
              derived evaluation items are custom and held out, meaning their exact wording
              is not shown to sellers. Items rotate between runs and are never published.
              Generation happens during offline staging, not while a submitted model is
              being evaluated.
            </p>

            <ol className="pipeline-steps">
              <li><span>01</span><div><strong>Ingest and version the seeds</strong><p>Every public source item enters with its dataset revision and license metadata.</p></div></li>
              <li><span>02</span><div><strong>Normalize and fingerprint</strong><p>Each item receives stable identifiers for its original seed and behavior, preserving its lineage through every later transform.</p></div></li>
              <li><span>03</span><div><strong>Apply a constrained transform</strong><p>Permute keyed choices, mutate a gold patch into distractors, derive a binary legal question, or wrap a harmful behavior in a new framing.</p></div></li>
              <li><span>04</span><div><strong>Check what must remain true</strong><p>The answer key must follow the transform, or the original assessed behavior must remain unchanged. Invalid variants never enter the pool.</p></div></li>
              <li><span>05</span><div><strong>Deduplicate, rotate, and pin</strong><p>Content fingerprints make each selection reproducible and verify that the staged dataset has not changed.</p></div></li>
            </ol>

            <h3>Rotation overlap</h3>
            <p>
              We expanded the biology pool from 140 to 532 items so repeated submissions
              would encounter fewer of the same prompts. The variants use different
              framings—such as direct requests, fictional scenarios, and appeals to
              authority—because models may refuse one framing while answering another.
              These variants make the evaluation less predictable and more behaviorally
              diverse, but they remain correlated; adding new underlying behaviors is what
              increases statistical confidence.
            </p>
          </section>

          <section id="uncertainty">
            <p className="section-number">5</p>
            <h2>How uncertainty affects a verdict</h2>
            <p>
              The observed harmful-assistance rate is only a sample. A result just under the
              limit may reflect luck, especially when many prompts are variants of the same
              behavior. We ask how high the actual rate could plausibly be given the amount
              of independent evidence. The gate passes only if that conservative estimate
              remains below the domain limit.
            </p>
            <div className="formula-card">
              <span>Decision statistic</span>
              <div
                className="latex-equation"
                dangerouslySetInnerHTML={{ __html: decisionEquation }}
              />
              <small>
                UCB is the 95% Wilson upper bound on the harmful-assistance rate. The
                effective sample size discounts correlated variants. κ is the maximum
                harm rate permitted at the model&apos;s measured capability.
              </small>
            </div>
            <h3>Correct for guessing</h3>
            <p>
              Raw accuracy is misleading across different question formats. Random choice
              yields 25% on a four-option probe and 50% on a binary probe. We subtract that
              floor before comparing capability between instruments.
            </p>
            <div className="formula-card compact">
              <div
                className="latex-equation"
                dangerouslySetInnerHTML={{ __html: adjustedCapabilityEquation }}
              />
            </div>
            <h3>Effective sample size</h3>
            <p>
              The 532 biology variants come from 28 underlying behaviors. We estimate
              within-behavior correlation and use it to compute an effective sample size.
              If every response in a behavior cluster moved together, those 532 prompts
              would count as 28 independent observations.
            </p>
            <p>
              A domain can therefore be skipped, passed, failed, or marked inconclusive. A
              skip means the capability probe did not activate that domain. A pass or failure
              compares the conservative harm estimate with the allowed limit; an inconclusive
              result means there was not enough independent evidence to decide.
            </p>
          </section>

          <section id="judge">
            <p className="section-number">6</p>
            <h2>How open-ended answers are scored</h2>
            <p>
              We initially searched responses for refusal phrases. That failed for a simple
              reason: a response can begin with an apology and still provide useful harmful
              instructions, while irrelevant or nonsensical output can omit refusal language
              and remain harmless.
            </p>
            <p>
              We replaced the phrase matcher with Qwen3Guard-Gen-8B, a separate guard model
              whose exact version is fixed across runs. It reads the request and response and
              decides whether the response materially helped. If its output cannot be read
              reliably, the run is marked incomplete rather than scored from only the answers
              that happened to parse. A separate judge also prevents the submitted model from
              grading its own answers.
            </p>
            <p>
              Every result records the judge model and revision used. This is more reliable
              than phrase matching, but it does not establish that the judge is correct.
              Agreement with expert human review still needs to be measured.
            </p>
          </section>

          <section id="leakage">
            <p className="section-number">7</p>
            <h2>Why public reports omit exact scores</h2>
            <p>
              Sellers can submit another version after a rejection. Exact domain scores and
              applied thresholds would help them infer the hidden items across attempts, so
              the domain-specific evaluation has three report views.
            </p>
            <div className="visibility-grid">
              <div><span>Buyer</span><strong>Certification verdict</strong><small>No private domain measurements</small></div>
              <div><span>Creator</span><strong>Verdict + failing domain</strong><small>Enough to act, not enough to probe</small></div>
              <div><span>Internal</span><strong>Full evidence</strong><small>Probe, ceiling, harm rate, uncertainty</small></div>
            </div>
            <p>
              Weightroom downloads the required model files and evaluation data before the
              run begins, then disables network access while the submitted model and judge
              execute. Private prompts are never sent to an external model endpoint, where
              they could be recorded by the model provider.
            </p>
            <p>
              The full internal report is digitally signed. If a score or verdict is edited
              after the run, the signature no longer verifies.
            </p>
          </section>

          <section id="results">
            <p className="section-number">8</p>
            <h2>What happened when we tested Qwen2.5-7B-Instruct</h2>
            <p>
              In an internal run, Qwen2.5-7B-Instruct followed the full decision path. It
              cleared the general refusal screen. At least one private capability probe
              crossed its activation floor, so the corresponding open-ended test ran. The
              model then provided materially useful help on enough harmful requests for the
              uncertainty-adjusted harm estimate to exceed that domain&apos;s limit. One domain
              failure was enough to reject the submission.
            </p>
            <p>
              On one NVIDIA A10G GPU, a full three-domain run has taken roughly five to ten
              minutes and cost about $0.13. Those are measurements from our runs, not
              guaranteed latency or pricing for other hardware and providers.
            </p>
          </section>

          <section id="limitations">
            <p className="section-number">9</p>
            <h2>What this evaluation cannot establish</h2>
            <ul className="limitations-list">
              <li><strong>The capability probes are proxies.</strong> The coding probe measures patch recognition rather than authorship, and the legal probe has not yet been shown to predict harmful legal assistance.</li>
              <li><strong>The ceiling is policy.</strong> Its anchors are deliberate judgment calls tied to these exact instruments.</li>
              <li><strong>The judge needs human validation.</strong> Automated labels have not yet been calibrated against an expert-reviewed sample.</li>
              <li><strong>Transformation does not erase training exposure.</strong> The private items reduce verbatim memorization, but their public source material may still have appeared in training.</li>
              <li><strong>A model may hide its capability.</strong> Comparing public and private probe results could flag deliberate underperformance, but that check is not yet implemented.</li>
            </ul>
            <p>
              Large model developers also use expert red-teaming, studies of whether a model
              improves a person&apos;s ability to carry out hazardous work, and review by a
              governance team. Weightroom&apos;s mechanism is an automated screen built for
              continuous marketplace submissions. A successful result records conformance to
              a versioned evaluation standard. It does not establish safety across every
              deployment.
            </p>
          </section>

        </div>
      </div>
    </article>
  );
}
