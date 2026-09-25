import { DEMO_START, GITHUB_URL, siteHref } from "../links";
import { SiteHeader } from "../SiteHeader";
import styles from "./Home.module.css";
import { Quickstart } from "./Quickstart";

const DASHBOARD = siteHref(DEMO_START);

/**
 * One run of the demo support agent, taken from the dashboard's sample data (the same trace the
 * demo shows), laid out as a receipt: the question, each step, what every model call cost.
 */
const RUN = {
  question: "Where's order 1291? Can you reschedule delivery to 10 am tomorrow?",
  answer:
    "Order 1291 hasn't shipped yet, so I've rescheduled the delivery for tomorrow at 10:00 am. You'll get a text when it's on the way.",
  steps: [
    { name: "chat claude-sonnet-5", kind: "llm", ms: 1572, cost: "$0.0045" },
    { name: "lookup_order", kind: "tool", ms: 22 },
    { name: "chat claude-sonnet-5", kind: "llm", ms: 1672, cost: "$0.0050" },
    { name: "reschedule_delivery", kind: "tool", ms: 99 },
    { name: "chat claude-sonnet-5", kind: "llm", ms: 2989, cost: "$0.0062" },
  ],
  total: { ms: 6386, cost: "$0.0158", tokens: "5.8k in · 422 out" },
} as const;

const TRY_THIS = ["tick Error under Status", "sort by Cost", "open a run to see its waterfall"];

const ROADMAP: [string, string, "done" | "next" | "later"][] = [
  ["Dashboard, sample data, CI", "M0", "done"],
  ["Python SDK and the live waterfall", "M1", "done"],
  ["Gateway for Claude Code and Copilot", "M2", "next"],
  ["Guardrails, budgets, cost reports, eval gate", "M3", "later"],
  ["Hosted demo, docs and a short video", "M4", "later"],
];

function seconds(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function Receipt() {
  const longest = Math.max(...RUN.steps.map((s) => s.ms));
  return (
    <figure className={styles.receipt} aria-label="One traced run of the demo agent">
      <div className={styles.receiptHead}>
        <span className={styles.receiptLabel}>support-agent.run</span>
        <span className={styles.receiptOk}>ok</span>
      </div>
      <p className={styles.said}>
        <span className={styles.who}>Customer</span>
        {RUN.question}
      </p>
      <ol className={styles.stepList}>
        {RUN.steps.map((s, i) => (
          <li key={i} className={styles.stepRow} data-kind={s.kind}>
            <span className={styles.stepName}>{s.name}</span>
            <span className={styles.stepBar}>
              <i style={{ width: `${Math.max(3, (s.ms / longest) * 100)}%` }} />
            </span>
            <span className={styles.stepTime}>{seconds(s.ms)}</span>
            <span className={styles.stepCost}>{"cost" in s ? s.cost : ""}</span>
          </li>
        ))}
      </ol>
      <p className={styles.said}>
        <span className={styles.who}>Agent</span>
        {RUN.answer}
      </p>
      <figcaption className={styles.receiptTotal}>
        <span>{seconds(RUN.total.ms)}</span>
        <span>{RUN.total.tokens}</span>
        <strong>{RUN.total.cost}</strong>
      </figcaption>
    </figure>
  );
}

export function Home() {
  return (
    <div className={styles.page}>
      <SiteHeader />
      <main>
        <section className={styles.hero}>
          <div className={styles.heroText}>
            <h1 className={styles.title}>See what your agent actually did.</h1>
            <p className={styles.lede}>
              LucentPad records every model call and tool step your agent makes and lays them out on
              a timeline, with the tokens, latency and cost of each one. It&apos;s open source and
              runs on your laptop.
            </p>
            <div className={styles.actions}>
              <a href={DASHBOARD} className={styles.primary}>
                Open the dashboard
              </a>
              <a href="#get-started" className={styles.secondary}>
                Get started <span aria-hidden="true">↓</span>
              </a>
            </div>
            <p className={styles.meta}>
              Python SDK for Anthropic and OpenAI · Claude Code and Copilot next · Apache-2.0
            </p>
          </div>
          <Receipt />
        </section>

        <section className={styles.showcase} aria-labelledby="showcase-title">
          <div className={styles.showcaseHead}>
            <h2 id="showcase-title" className={styles.sectionTitle}>
              Try the dashboard
            </h2>
            <a href={DASHBOARD} className={styles.textLink}>
              Open full screen <span aria-hidden="true">↗</span>
            </a>
          </div>
          <div className={styles.window}>
            <iframe
              title="LucentPad dashboard with sample data"
              src={DASHBOARD}
              className={styles.frame}
              loading="lazy"
            />
          </div>
          <p className={styles.caption}>
            Running in your browser on a week of sample traffic. Things to try:{" "}
            {TRY_THIS.join(" · ")}.
          </p>
        </section>

        <section id="get-started" className={styles.section}>
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>Get started in minutes</h2>
            <p className={styles.sectionText}>
              Clone it, run one command, and the dashboard is up with sample data. Then point your
              own agent at it.
            </p>
          </div>
          <Quickstart />
        </section>

        <section className={styles.section} aria-labelledby="roadmap-title">
          <div className={styles.sectionHead}>
            <h2 id="roadmap-title" className={styles.sectionTitle}>
              Built in the open
            </h2>
            <p className={styles.sectionText}>
              LucentPad is being built one milestone at a time. Here&apos;s where it stands.
            </p>
          </div>
          <ol className={styles.roadmap}>
            {ROADMAP.map(([what, milestone, state]) => (
              <li key={milestone} data-state={state}>
                <span className={styles.mark} aria-hidden="true" />
                <span className={styles.milestone}>{milestone}</span>
                <span className={styles.what}>{what}</span>
                <span className={styles.state}>
                  {state === "done" ? "Done" : state === "next" ? "Up next" : "Planned"}
                </span>
              </li>
            ))}
          </ol>
        </section>
      </main>
      <footer className={styles.footer}>
        <span>LucentPad · Apache-2.0</span>
        <a href={GITHUB_URL}>Source on GitHub</a>
      </footer>
    </div>
  );
}
