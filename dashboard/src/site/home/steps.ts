import { GITHUB_URL } from "../links";

export interface Step {
  id: string;
  label: string;
  soon?: string;
  intro: string;
  code: string;
}

const REPO = `${GITHUB_URL}.git`;

/** The basic setup, on the landing page itself. The full guide expands below it. */
export const STEPS: [Step, ...Step[]] = [
  {
    id: "local",
    label: "Run it locally",
    intro:
      "You need Docker, Python 3.12 with uv, and Node 24. The dashboard opens with a week of sample traffic already in it.",
    code: `git clone ${REPO}\ncd lucentpad\nmake install\nmake dev   # → http://localhost:5173`,
  },
  {
    id: "sdk",
    label: "Trace your agent",
    intro:
      "Wrap your Anthropic or OpenAI client once. Every call it makes shows up with its tokens, latency and cost.",
    code: `pip install "./sdk[anthropic]"\n\nimport lucentpad\nfrom anthropic import Anthropic\n\nlucentpad.init(service_name="my-agent")\nclient = lucentpad.wrap(Anthropic())`,
  },
  {
    id: "demo-agent",
    label: "Watch a demo run",
    intro:
      "A small support agent for a pretend store. Mock mode needs no API key and costs nothing; keep the dashboard open and watch the run draw.",
    code: `make agent Q="Where's order 1042? I want a refund." ARGS=--mock-llm`,
  },
  {
    id: "gateway",
    label: "Claude Code & Copilot",
    soon: "next",
    intro:
      "Coming next: point Claude Code or Copilot at the gateway and each session shows up with the cost of every turn.",
    code: `ANTHROPIC_BASE_URL=http://localhost:8000/gateway/anthropic claude`,
  },
];
