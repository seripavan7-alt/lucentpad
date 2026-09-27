import { useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./GatewaySetup.module.css";
import { CLAUDE_CODE_SETUP, COPILOT_CLI_SETUP, OPENAI_BASE } from "./setup";

type CopyState = "idle" | "copied" | "failed";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    let next: CopyState = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setState("idle");
    }, 1500);
  };

  return (
    <button
      type="button"
      className={styles.copy}
      aria-label={`Copy ${label}`}
      data-state={state}
      onClick={() => {
        void copy();
      }}
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
    </button>
  );
}

function Code({ text, label }: { text: string; label: string }) {
  return (
    <div className={styles.code}>
      <pre>
        <code>{text}</code>
      </pre>
      <CopyButton text={text} label={label} />
    </div>
  );
}

function Client({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.client} aria-label={title}>
      <h3 className={styles.clientTitle}>{title}</h3>
      {children}
    </section>
  );
}

/**
 * Empty state of the Gateway page: exactly how to point each supported client at the
 * gateway (docs/handoff/M2.md, "Client setup"), with copy buttons.
 */
export function GatewaySetup({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className={styles.setup}>
      <div className={styles.intro}>
        <h2 className={styles.title}>{title}</h2>
        <p>
          Point a coding assistant at the LucentPad gateway and each turn shows up here within a few
          seconds, with tokens, cost and latency. Requests and streams pass through unchanged; your
          key is forwarded, never stored.
        </p>
        {action}
      </div>

      <div className={styles.clients}>
        <Client title="Claude Code">
          <p>
            Set this before starting <code>claude</code>. In VS Code, add it to{" "}
            <code>claudeCode.environmentVariables</code>.
          </p>
          <Code text={CLAUDE_CODE_SETUP} label="Claude Code setup" />
        </Client>

        <Client title="Copilot CLI">
          <p>
            Bring your own Anthropic key, then start <code>copilot</code>.
          </p>
          <Code text={COPILOT_CLI_SETUP} label="Copilot CLI setup" />
        </Client>

        <Client title="Copilot Chat in VS Code">
          <ol className={styles.steps}>
            <li>
              In the chat model picker, open <strong>Manage Models</strong> and add a{" "}
              <strong>Custom Endpoint</strong>.
            </li>
            <li>
              Base URL:
              <Code text={OPENAI_BASE} label="Copilot Chat base URL" />
            </li>
            <li>API key: your OpenAI key.</li>
          </ol>
        </Client>
      </div>

      <p className={styles.note}>
        Copilot is traced only when it uses your own key (BYOK). Requests to GitHub-hosted models
        and inline completions never pass through the gateway.
      </p>
    </div>
  );
}
