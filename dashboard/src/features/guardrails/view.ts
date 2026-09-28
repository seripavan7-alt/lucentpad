/*
 * The Guardrails page's view state, kept in the URL:
 *   ?range=7d          time-range preset for the counts and the event list (default 24h, omitted)
 *   ?kind=block        only blocks, redactions (kind=redaction) or budget alerts (kind=budget);
 *                      omitted = all
 * Paging is not in the URL: any change here starts again from the newest events.
 */
import { useSearchParams } from "react-router";
import {
  GUARDRAIL_EVENT_KINDS,
  type GuardrailEvent,
  type GuardrailEventKind,
} from "../../api/types";
import { formatCost } from "../../lib/format";
import { isoMicros } from "../../lib/time";
import { DEFAULT_RANGE, RANGES, type RangeId } from "../traces/view";

export interface GuardrailsView {
  range: RangeId;
  /** null = every kind. */
  kind: GuardrailEventKind | null;
}

export function parseGuardrailsView(params: URLSearchParams): GuardrailsView {
  const rawRange = params.get("range");
  const range = RANGES.find((r) => r.id === rawRange)?.id ?? DEFAULT_RANGE;
  const rawKind = params.get("kind");
  const kind = GUARDRAIL_EVENT_KINDS.find((k) => k === rawKind) ?? null;
  return { range, kind };
}

export function applyGuardrailsPatch(
  prev: URLSearchParams,
  patch: Partial<GuardrailsView>,
): URLSearchParams {
  const next = new URLSearchParams(prev);
  if (patch.range !== undefined) {
    if (patch.range === DEFAULT_RANGE) next.delete("range");
    else next.set("range", patch.range);
  }
  if (patch.kind !== undefined) {
    if (patch.kind === null) next.delete("kind");
    else next.set("kind", patch.kind);
  }
  return next;
}

export function useGuardrailsView(): [GuardrailsView, (patch: Partial<GuardrailsView>) => void] {
  const [params, setParams] = useSearchParams();
  const view = parseGuardrailsView(params);
  const update = (patch: Partial<GuardrailsView>) => {
    setParams((prev) => applyGuardrailsPatch(prev, patch));
  };
  return [view, update];
}

/** The kind filter as request params (repeated `kind`, here at most one). */
export function kindParams(view: GuardrailsView): { kind?: GuardrailEventKind[] } {
  return view.kind === null ? {} : { kind: [view.kind] };
}

/**
 * Events carry no id: a block is its guardrail span, a redaction or budget alert is one event on
 * a span, so span + kind + detail + time identifies it.
 */
export function eventKey(e: GuardrailEvent): string {
  return [e.kind, e.trace_id, e.span_id, e.rule ?? e.redaction_kind ?? "", e.time].join(".");
}

/** Newest first; ties by key so the order is stable. */
export function byTimeDesc(a: GuardrailEvent, b: GuardrailEvent): number {
  const d = isoMicros(b.time) - isoMicros(a.time);
  if (d !== 0) return d;
  const ka = eventKey(a);
  const kb = eventKey(b);
  return ka < kb ? 1 : ka > kb ? -1 : 0;
}

/** Where an event opens: its trace with the span selected. */
export const eventHref = (e: Pick<GuardrailEvent, "trace_id" | "span_id">) =>
  `/traces/${e.trace_id}?span=${e.span_id}`;

const REDACTION_LABELS: Record<string, string> = {
  email: "Email",
  api_key: "API key",
  card: "Card number",
  phone: "Phone number",
};

export function redactionLabel(kind: string | null | undefined): string {
  if (!kind) return "Value";
  return REDACTION_LABELS[kind] ?? kind.replace(/_/g, " ");
}

const REDACTION_NOUNS: Record<string, [string, string]> = {
  email: ["email", "emails"],
  api_key: ["API key", "API keys"],
  card: ["card number", "card numbers"],
  phone: ["phone number", "phone numbers"],
};

/** "12 emails", "1 API key": a count of redacted values of one kind. */
export function redactionCount(kind: string, count: number): string {
  const [one, many] = REDACTION_NOUNS[kind] ?? [kind.replace(/_/g, " "), kind.replace(/_/g, " ")];
  return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
}

/** What a budget alert's budget covered: its `budget_scope`, else what its source implies (an
 * SDK budget covers one run, the gateway's one session). */
export function budgetScope(e: Pick<GuardrailEvent, "budget_scope" | "source">): string {
  return e.budget_scope ?? (e.source === "gateway" ? "session" : "run");
}

/** "spent $0.62 of $0.50": a budget alert's spend against its limit (either may be missing). */
export function budgetSpend(
  e: Pick<GuardrailEvent, "budget_spent_usd" | "budget_limit_usd">,
): string {
  const spent = e.budget_spent_usd;
  const limit = e.budget_limit_usd;
  if (spent != null && limit != null) return `spent ${formatCost(spent)} of ${formatCost(limit)}`;
  if (spent != null) return `spent ${formatCost(spent)}`;
  if (limit != null) return `over the ${formatCost(limit)} limit`;
  return "over budget";
}
