const intFormat = new Intl.NumberFormat("en-US");

/** USD with precision that suits LLM costs: 4 decimals below $1, 2 above. */
export function formatCost(usd: number): string {
  if (!Number.isFinite(usd) || usd === 0) return "$0.00";
  if (usd < 0) return `-${formatCost(-usd)}`;
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Compact token counts: 842, 12.3k, 123k, 1.2M. */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n)) return "–";
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  if (abs < 100_000) return `${trimZero((Math.round(n / 100) / 10).toFixed(1))}k`;
  if (abs < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${trimZero((Math.round(n / 100_000) / 10).toFixed(1))}M`;
}

export function formatInteger(n: number): string {
  return intFormat.format(n);
}

/** Durations: 0ms, 842ms, 1.24s, 12.3s, 2m 05s, 1h 02m. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "–";
  if (ms < 1) return ms === 0 ? "0ms" : "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(2)}s`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const totalSeconds = Math.round(s);
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}m ${pad(totalSeconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${pad(minutes % 60)}m`;
}

/** "just now", "42s ago", "5m ago", "3h ago", "2d ago", then a short date. */
export function formatRelative(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "–";
  const diff = Math.max(0, now - t) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 30 * 86_400) return `${Math.floor(diff / 86_400)}d ago`;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatDateTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function shortId(id: string, length = 8): string {
  return id.slice(0, length);
}

const CLIENT_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  "copilot-chat": "Copilot Chat",
  "copilot-cli": "Copilot CLI",
  sdk: "SDK",
};

export function clientLabel(client: string | null | undefined): string | null {
  if (!client) return null;
  return CLIENT_LABELS[client] ?? client;
}

/** Who produced a trace: the agent's service name for SDK traces, the client for gateway ones. */
export function traceOrigin(trace: {
  source: string;
  client?: string | null;
  service_name?: string | null;
}): string | null {
  if (trace.source === "sdk") return trace.service_name ?? null;
  return clientLabel(trace.client) ?? trace.service_name ?? null;
}

function trimZero(s: string): string {
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
