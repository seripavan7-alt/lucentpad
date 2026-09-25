import { marked } from "marked";
import { useMemo, useState } from "react";
import guide from "../../../../docs/getting-started.md?raw";
import { CopyIcon } from "../../components/icons";
import styles from "./Home.module.css";
import { STEPS, type Step } from "./steps";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={styles.copy}
      onClick={() => {
        // The clipboard needs a secure context (https or localhost); do nothing elsewhere.
        if (!window.isSecureContext) return;
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => {
            setCopied(false);
          }, 1500);
        });
      }}
    >
      <CopyIcon width={12} height={12} />
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/** The basic setup as tabs, with the complete guide one click away underneath. */
export function Quickstart() {
  const [first] = STEPS;
  const [active, setActive] = useState(first.id);
  const step: Step = STEPS.find((s) => s.id === active) ?? first;
  // Our own markdown from the repo (docs/getting-started.md); nothing user-supplied.
  const html = useMemo(() => marked.parse(guide, { async: false }), []);
  return (
    <div className={styles.quickstart}>
      <div className={styles.tabs} role="tablist" aria-label="Quick start">
        {STEPS.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={s.id === active}
            className={styles.tab}
            onClick={() => {
              setActive(s.id);
            }}
          >
            {s.label}
            {s.soon && <span className={styles.soon}>{s.soon}</span>}
          </button>
        ))}
      </div>
      <div className={styles.tabPanel} role="tabpanel" aria-label={step.label}>
        <p className={styles.tabIntro}>{step.intro}</p>
        <div className={styles.codeWrap}>
          <pre className={styles.code}>
            <code>{step.code}</code>
          </pre>
          <CopyButton text={step.code} />
        </div>
      </div>
      <details className={styles.fullGuide}>
        <summary>Full setup guide</summary>
        <article className={styles.prose} dangerouslySetInnerHTML={{ __html: html }} />
      </details>
    </div>
  );
}
