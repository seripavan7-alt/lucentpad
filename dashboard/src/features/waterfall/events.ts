import { EventName } from "../../api/types";

export type EventTone = "warning" | "blocked" | "info" | "neutral";

const EVENTS: Record<string, { label: string; tone: EventTone }> = {
  [EventName.FAILOVER]: { label: "Failover", tone: "warning" },
  [EventName.BUDGET_ALERT]: { label: "Budget alert", tone: "warning" },
  [EventName.REDACTION]: { label: "Redaction", tone: "info" },
  [EventName.GUARDRAIL_BLOCK]: { label: "Guardrail block", tone: "blocked" },
};

export function eventLabel(name: string): string {
  return EVENTS[name]?.label ?? name;
}

export function eventTone(name: string): EventTone {
  return EVENTS[name]?.tone ?? "neutral";
}
