import { Attr, type Span } from "../../api/types";

/** Secondary label for a span row: the model, tool or guardrail rule. */
export function spanSubtitle(span: Span): string | null {
  const a = span.attributes ?? {};
  const value =
    span.kind === "llm"
      ? (a[Attr.GEN_AI_RESPONSE_MODEL] ?? a[Attr.GEN_AI_REQUEST_MODEL])
      : span.kind === "tool"
        ? a[Attr.GEN_AI_TOOL_NAME]
        : span.kind === "guardrail"
          ? a[Attr.GUARDRAIL_RULE]
          : undefined;
  if (value === undefined) return null;
  const text = Array.isArray(value) ? value.join(", ") : String(value);
  // Span names often already carry it ("chat claude-sonnet-5"); don't repeat it.
  return span.name.includes(text) ? null : text;
}
