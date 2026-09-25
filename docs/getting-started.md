# Getting started with LucentPad

LucentPad records every LLM call your agents make and shows each run as a live trace, with tokens,
latency, cost and guardrail events. It connects two ways:

- **SDK mode (Python):** wrap your Anthropic or OpenAI client in one line and every call is traced.
- **Gateway mode:** point Claude Code or Copilot at LucentPad's gateway; it forwards traffic to the
  provider and records each turn on the way through.

> **Project status:** LucentPad is being built in the open. The dashboard, the Python SDK and the
> demo agent work today. The gateway for Claude Code and Copilot arrives in milestone M2.

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

- **Traces** lists every run (last 15 minutes by default; pick a range top right). Filter by name,
  status, source, client, model and service on the left; new runs appear live.
- **Trace detail** shows the waterfall: every model call and tool step on one time axis, drawing live
  while the run is going, with the cost of each model call. Click a span to see its tokens, cost,
  latency, input and output.
- **Costs, Gateway, Guardrails, Evals** are placeholders until milestones M2 and M3.

## 3. Watch the demo agent

A customer-support agent for a fictional store, over fake data. With `make dev` running:

```sh
make agent Q="Where's order 1042? I want a refund." ARGS=--mock-llm   # free, no API key
make agent Q="Where's order 1042? I want a refund."                   # real Claude; needs ANTHROPIC_API_KEY
```

Open the trace link it prints and watch the waterfall draw step by step.

## 4. Trace your own agent (SDK mode)

The SDK isn't on PyPI yet; install it from the repo:

```sh
pip install "./sdk[anthropic]"   # or [openai]
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

## 5. Trace Claude Code or Copilot (gateway mode, coming in M2)

Point the assistant's API base URL at the gateway; your API key is forwarded to the provider, never
stored:

```sh
ANTHROPIC_BASE_URL=http://localhost:8000/gateway/anthropic claude
```

Each session appears live with tokens and cost per turn.

## 6. Contribute

Run `make check` (lint, types, tests, build) before opening a pull request. Tests never call real LLM
APIs.
