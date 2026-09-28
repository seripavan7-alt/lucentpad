# Evals

`lucentpad eval` runs saved cases against an agent, checks each answer and its traced tool calls,
compares the run with `baseline.json`, and fails on a regression. CI runs it on pull requests
that touch the agent, its evals or the SDK (`.github/workflows/eval.yml`).

```sh
uv run lucentpad eval evals/support_agent.yaml                 # real model (needs ANTHROPIC_API_KEY)
uv run lucentpad eval evals/support_agent.yaml --mock          # scripted model: free, offline
uv run lucentpad eval evals/support_agent.yaml --mock --update-baseline   # accept the results
```

Options: `--baseline FILE` (default `baseline.json` next to the suite), `--update-baseline`,
`--max-cost USD` (stop and fail once the run has spent that much), `--endpoint URL` (default
`$LUCENTPAD_ENDPOINT` or `http://localhost:8000`), `--model NAME`, `--no-post`, `--mock`.

## Suite format

```yaml
suite: support_agent                  # name on the Evals page
target: support_agent.evals:run_case  # called as run_case(input, model=..., mock=...)
model: claude-haiku-4-5
service: support-agent                # service.name on the spans
rules: ../examples/support_agent/support_agent/rules.yaml  # for --mock / API down
cases:
  - id: refund_over_limit_blocked
    input: "My espresso machine from order 1057 stopped working. I want a refund."
    checks:
      - tool_called: lookup_order
      - no_tool: issue_refund
      - contains: colleague
```

The target returns `{"answer": ..., "trace_id": ...}` (or just the answer). `lucentpad eval`
initialises the SDK itself and runs each case inside its own trace, whose root span is tagged
with `lucentpad.eval.run_id` and `lucentpad.eval.case`. The target doesn't call `lucentpad.init`.

## Checks

| Check | Passes when |
| --- | --- |
| `contains: text` / `not_contains: text` | the answer contains / doesn't contain it (case-insensitive) |
| `regex: pattern` | `re.search` finds it in the answer |
| `tool_called: name` | a `kind="tool"` span with that name exists (any status) |
| `tool_called: {name, args: {...}}` | ...and one call's arguments include these (`"1042"` matches `1042`) |
| `no_tool: name` / `no_tool` | that tool / no tool ran |
| `max_cost_usd: 0.05` | the case's cost is at most that (unknown cost fails) |
| `max_latency_ms: 4000` | the target returned within that time |

Tool calls and cost come from the case's trace. A call blocked by a guardrail never ran, so it
is a `kind="guardrail"` span and doesn't count as called. Arguments come from the tool span's
input preview (JSON), which is redacted: don't match on emails or keys.

## Where the results come from

- **API up:** after each case the SDK flushes and the CLI reads the trace back from
  `GET /v1/traces/{id}`. Cost is the server's. The run is posted to `POST /v1/evals/runs` for
  the Evals page (git sha, ref and CI link come from `GITHUB_*` in Actions, otherwise from the
  local checkout). If the post fails, the exit code stays the same.
- **`--mock` or API down:** the CLI checks the spans the SDK recorded in-process. Cost is
  estimated from token counts at the server's list prices. Guardrail rules come from the suite's
  `rules:`. With `--mock` the spans still go to the API and the run is still posted if the API
  is up (add `--no-post` to skip the post).

## The gate

Exit 1 when a case that passed in the baseline fails, when the total cost is more than 1.25x the
baseline's, or when `--max-cost` is reached. Otherwise exit 0, which includes new cases and
cases that already failed in the baseline (they're reported, not gated). Exit 2 for a bad suite
or baseline file. `--update-baseline` writes the file and exits 0.

The committed baseline was recorded with `--mock`, so a real-model run compares pass/fail only,
not cost (the scripted model's token counts say nothing about a real one). Once the
`ANTHROPIC_API_KEY` secret exists, record a real baseline with
`lucentpad eval evals/support_agent.yaml --update-baseline` and commit it.

## Demo step 8

`demo-break-prompt.patch` changes the agent's system prompt so it stops looking orders up. To
show the gate failing, apply it on a branch and open a PR:

```sh
git switch -c shorter-replies && git apply evals/demo-break-prompt.patch
git commit -am "Shorter replies" && git push -u origin shorter-replies   # then open the PR
```

Locally, `lucentpad eval evals/support_agent.yaml --mock` already fails with the patch applied:
the scripted model follows the same line of the prompt.
