import type { SpanStatus } from "../api/types";

const STATUS_LABELS: Record<SpanStatus, string> = { ok: "OK", error: "Error", blocked: "Blocked" };

export function statusLabel(status: SpanStatus): string {
  return STATUS_LABELS[status];
}
