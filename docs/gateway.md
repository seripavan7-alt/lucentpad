# Tracing Claude Code and Copilot through the LucentPad gateway

The gateway is a small proxy inside the LucentPad API. You point a coding assistant's base URL at it;
it forwards each request to Anthropic or OpenAI unchanged, streams the answer back chunk by chunk as it
arrives, and records the turn (model, tokens, latency, cost) on the side. Each session shows up as one
trace, on the **Gateway** page and in **Traces**.

Your API key is forwarded to the provider and never stored or logged. LucentPad keeps only a salted
fingerprint of it, to group turns into sessions.

With `make dev` running, the gateway lives at `http://localhost:8000/gateway/…`.

## What can be traced

| Client | Traced | Why |
| --- | --- | --- |
| Claude Code (terminal and VS Code extension) | Every request | It supports a custom base URL (`ANTHROPIC_BASE_URL`). |
| Copilot CLI | Only when it uses **your own key** | Copilot's GitHub-hosted models can't be pointed anywhere else; its "bring your own key" (BYOK) mode can. |
| Copilot Chat in VS Code | Only BYOK chat, via the **Custom Endpoint** provider | Same reason. Inline completions never go through a custom endpoint. |

## Claude Code

```sh
export ANTHROPIC_BASE_URL=http://localhost:8000/gateway/anthropic
export ANTHROPIC_API_KEY=sk-ant-…      # your key, sent to Anthropic via the gateway
claude
```

- Leave `/v1` off: Claude Code adds `/v1/messages` itself.
- The variables are read at startup, so restart Claude Code after changing them. To keep them, add
  the lines to `~/.zshrc`, or put them in the `env` block of `~/.claude/settings.json`.
- VS Code extension: set them in the `claudeCode.environmentVariables` setting.

## Copilot CLI

```sh
export COPILOT_PROVIDER_TYPE=anthropic
export COPILOT_PROVIDER_BASE_URL=http://localhost:8000/gateway/anthropic
export COPILOT_PROVIDER_API_KEY=sk-ant-…
export COPILOT_MODEL=claude-sonnet-5
copilot
```

To use OpenAI instead: `COPILOT_PROVIDER_TYPE=openai`,
`COPILOT_PROVIDER_BASE_URL=http://localhost:8000/gateway/openai/v1`, an OpenAI key and e.g.
`COPILOT_MODEL=gpt-5`. The model must support streaming and tool calls.

## Copilot Chat in VS Code

1. Open Copilot Chat, open the model picker and choose **Manage Models…**.
2. Add a **Custom Endpoint** (OpenAI-compatible) provider:
   - Base URL: `http://localhost:8000/gateway/openai/v1`
   - API key: your OpenAI key
   - Model: e.g. `gpt-5` (it must support tool calls)
3. Pick that model in the chat's model picker and ask something.

On Copilot Business or Enterprise, your organisation's policy has to allow "Bring Your Own Language Model
Key in VS Code".

## Check it works

```sh
curl -s http://localhost:8000/gateway/anthropic/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5","max_tokens":32,"messages":[{"role":"user","content":"Say hi"}]}'
```

The reply is Anthropic's, unchanged. Open the **Gateway** page: the turn is there with its tokens and
cost within a couple of seconds.

## Failover

For requests that are **not** streamed, a 429 or 5xx from the provider is retried once, then sent once
more on a smaller model from the same provider (`claude-sonnet-5` → `claude-haiku-4-5`,
`claude-opus-5-5` → `claude-sonnet-5`, `gpt-5` → `gpt-5-mini`). The switch is recorded on the trace.
Streamed requests (almost all of Claude Code's) are never retried: part of the answer may already have
reached you. Turn failover off with `LUCENTPAD_GATEWAY_FAILOVER=0`.

## Troubleshooting

- **Nothing shows up.** Check the variable is set in the shell that started the tool (`echo
  $ANTHROPIC_BASE_URL`), and restart the tool after changing it.
- **401 from the provider.** The gateway forwards your key as-is; the key itself is wrong or missing.
- **404 from the gateway.** The base URL has an extra or missing `/v1`: Claude Code and Copilot CLI
  (Anthropic) take `…/gateway/anthropic`; OpenAI-compatible clients take `…/gateway/openai/v1`.
