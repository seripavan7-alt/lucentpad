# Getting started with LucentPad

LucentPad records every LLM call your agents make and shows each run as a live trace, with tokens,
latency, cost and guardrail events. It connects two ways:

- **SDK mode (Python):** wrap your Anthropic or OpenAI client in one line and every call is traced.
- **Gateway mode:** point Claude Code or Copilot at LucentPad's gateway; it forwards traffic to the
  provider and records each turn on the way through.

> **Project status:** LucentPad is being built in the open. The dashboard, the query API and the
> sample data work today. The SDK arrives in milestone M1 and the gateway in M2; those sections are
> marked below.

## 1. Run it locally

You need [Docker](https://docs.docker.com/get-docker/) (or OrbStack on macOS), Python 3.12 with
[uv](https://docs.astral.sh/uv/), and Node.js 24.

```sh
git clone https://github.com/seripavan7-alt/lucentpad.git
cd lucentpad
make install   # Python and dashboard dependencies
make dev       # Postgres, API and dashboard
```

Open <http://localhost:5173>. The first start loads a week of realistic sample traffic: support-agent
runs and Claude Code / Copilot sessions, including errors, a guardrail block and failovers.

`make down` stops the stack and wipes its data; the sample reloads on the next `make dev`.

## 2. Find your way around the dashboard

- **Traces** lists every run, newest first. Filter by source (SDK or gateway) and status.
- **Trace detail** shows the waterfall: every model call and tool step on one time axis. Click a span to
  see its tokens, cost, latency and attributes in the inspector.
- **Costs, Gateway, Guardrails, Evals** are placeholders until milestones M2 and M3.

## 3. Trace your own agent (SDK mode, coming in M1)

```sh
pip install lucentpad-sdk
```

```python
import lucentpad
from anthropic import Anthropic

lucentpad.init(endpoint="http://localhost:8000", service_name="support-agent")
client = lucentpad.wrap(Anthropic())  # same client, now traced

with lucentpad.trace("refund request"):
    client.messages.create(model="claude-sonnet-5", max_tokens=512, messages=[...])
```

Mark tool steps with `@lucentpad.span`. The exporter batches spans in the background and never blocks or
crashes your agent.

## 4. Trace Claude Code or Copilot (gateway mode, coming in M2)

Point the assistant's API base URL at the gateway; your API key is forwarded to the provider, never
stored:

```sh
ANTHROPIC_BASE_URL=http://localhost:8000/gateway/anthropic claude
```

Each session appears live with tokens and cost per turn.

## 5. Contribute

Run `make check` (lint, types, tests, build) before opening a pull request. Tests never call real LLM
APIs.
