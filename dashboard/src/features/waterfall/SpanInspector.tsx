import { Attr, type AttrValue, type Span } from "../../api/types";
import { CloseIcon } from "../../components/icons";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCost, formatDateTime, formatDuration, formatInteger } from "../../lib/format";
import { eventLabel, eventTone } from "./events";
import type { WaterfallRow } from "./layout";
import { PreviewBlock } from "./PreviewBlock";
import styles from "./SpanInspector.module.css";

function display(value: AttrValue | undefined): string {
  if (value === undefined) return "–";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function text(value: AttrValue | undefined): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function num(value: AttrValue | undefined): number | undefined {
  return typeof value === "number" ? value : undefined;
}

const KIND_LABELS: Record<Span["kind"], string> = {
  agent: "Agent",
  llm: "LLM call",
  tool: "Tool",
  guardrail: "Guardrail",
};

export function SpanInspector({ row, onClose }: { row: WaterfallRow; onClose: () => void }) {
  const { span } = row;
  const attrs = span.attributes ?? {};
  const inputTokens = num(attrs[Attr.GEN_AI_INPUT_TOKENS]);
  const outputTokens = num(attrs[Attr.GEN_AI_OUTPUT_TOKENS]);
  const cost = num(attrs[Attr.COST_USD]);
  const model = attrs[Attr.GEN_AI_RESPONSE_MODEL] ?? attrs[Attr.GEN_AI_REQUEST_MODEL];
  const requestModel = attrs[Attr.GEN_AI_REQUEST_MODEL];
  const events = span.events ?? [];
  const input = text(attrs[Attr.INPUT_PREVIEW]);
  const output = text(attrs[Attr.OUTPUT_PREVIEW]);
  const showContent = span.kind === "llm";
  // The previews get their own blocks; don't repeat them in the attribute list.
  const shownAbove = new Set<string>(showContent ? [Attr.INPUT_PREVIEW, Attr.OUTPUT_PREVIEW] : []);
  const attrEntries = Object.entries(attrs)
    .filter(([k]) => !shownAbove.has(k))
    .sort(([a], [b]) => a.localeCompare(b));

  const overview: [string, string][] = [
    ["Kind", KIND_LABELS[span.kind]],
    ["Latency", formatDuration(row.durationMs)],
    ["Started", `+${formatDuration(row.startMs)} · ${formatDateTime(span.start_time)}`],
  ];
  if (model !== undefined) {
    const modelText =
      requestModel !== undefined && display(requestModel) !== display(model)
        ? `${display(model)} (requested ${display(requestModel)})`
        : display(model);
    overview.push(["Model", modelText]);
  }
  if (inputTokens !== undefined || outputTokens !== undefined) {
    overview.push(["Tokens in", inputTokens === undefined ? "–" : formatInteger(inputTokens)]);
    overview.push(["Tokens out", outputTokens === undefined ? "–" : formatInteger(outputTokens)]);
  }
  if (cost !== undefined) overview.push(["Cost", formatCost(cost)]);
  overview.push(["Source", span.source === "sdk" ? "SDK" : "Gateway"]);

  return (
    <aside className={styles.inspector} aria-label="Span details">
      <header className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.kind} data-kind={span.kind}>
            {KIND_LABELS[span.kind]}
          </span>
          <h2 className={styles.title}>{span.name}</h2>
        </div>
        <button type="button" className={styles.close} onClick={onClose} aria-label="Close details">
          <CloseIcon />
        </button>
      </header>

      <div className={styles.status}>
        <StatusBadge status={span.status} />
        {span.status_message && <p className={styles.statusMessage}>{span.status_message}</p>}
      </div>

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Overview</h3>
        <dl className={styles.list}>
          {overview.map(([k, v]) => (
            <div key={k} className={styles.item}>
              <dt>{k}</dt>
              <dd className="num">{v}</dd>
            </div>
          ))}
          <div className={styles.item}>
            <dt>Span ID</dt>
            <dd className="mono">{span.span_id}</dd>
          </div>
          {span.parent_span_id && (
            <div className={styles.item}>
              <dt>Parent</dt>
              <dd className="mono">{span.parent_span_id}</dd>
            </div>
          )}
        </dl>
      </section>

      {showContent && (
        <section className={styles.section}>
          {input === undefined && output === undefined ? (
            <p className={styles.none}>Input and output not captured</p>
          ) : (
            <>
              {input !== undefined && (
                <PreviewBlock
                  label="Input"
                  text={input}
                  truncated={attrs[Attr.INPUT_TRUNCATED] === true}
                />
              )}
              {output !== undefined && (
                <PreviewBlock
                  label="Output"
                  text={output}
                  truncated={attrs[Attr.OUTPUT_TRUNCATED] === true}
                />
              )}
            </>
          )}
        </section>
      )}

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>
          Events {events.length > 0 && <span>{events.length}</span>}
        </h3>
        {events.length === 0 ? (
          <p className={styles.none}>No events</p>
        ) : (
          <ul className={styles.events}>
            {row.events.map(({ event, offsetMs }, i) => (
              <li key={`${event.name}-${i}`} className={styles.event}>
                <div className={styles.eventHead}>
                  <span
                    className={styles.eventDot}
                    data-tone={eventTone(event.name)}
                    aria-hidden="true"
                  />
                  <span className={styles.eventName}>{eventLabel(event.name)}</span>
                  <span className="muted num">+{formatDuration(offsetMs)}</span>
                </div>
                {Object.keys(event.attributes ?? {}).length > 0 && (
                  <dl className={styles.kv}>
                    {Object.entries(event.attributes ?? {}).map(([k, v]) => (
                      <div key={k}>
                        <dt className="mono">{k}</dt>
                        <dd className="mono">{display(v)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>
          Attributes {attrEntries.length > 0 && <span>{attrEntries.length}</span>}
        </h3>
        {attrEntries.length === 0 ? (
          <p className={styles.none}>No attributes</p>
        ) : (
          <dl className={styles.kv}>
            {attrEntries.map(([k, v]) => (
              <div key={k}>
                <dt className="mono">{k}</dt>
                <dd className="mono">{display(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
    </aside>
  );
}
