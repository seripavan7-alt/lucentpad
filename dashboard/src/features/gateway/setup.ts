/* Exact client setup for the gateway (docs/handoff/M2.md, "Client setup"). */

/** Where `make dev` serves the API (and so the gateway). */
export const GATEWAY_ORIGIN = "http://localhost:8000";
export const ANTHROPIC_BASE = `${GATEWAY_ORIGIN}/gateway/anthropic`;
export const OPENAI_BASE = `${GATEWAY_ORIGIN}/gateway/openai/v1`;

export const CLAUDE_CODE_SETUP = `export ANTHROPIC_BASE_URL=${ANTHROPIC_BASE}`;
export const COPILOT_CLI_SETUP = [
  "export COPILOT_PROVIDER_TYPE=anthropic",
  `export COPILOT_PROVIDER_BASE_URL=${ANTHROPIC_BASE}`,
  'export COPILOT_PROVIDER_API_KEY="$ANTHROPIC_API_KEY"',
  "export COPILOT_MODEL=claude-sonnet-5",
].join("\n");
