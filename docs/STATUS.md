# LucentPad status

**New agent? Read `CLAUDE.md` first, then this file, then `docs/handoff/M3.md` (M1/M2 are history).**
Source of truth: `docs/PRD.md`. Process: plan → "go" → build → checkpoint → "approved" → commit `M<n>: <title>`.

## Now
- **Milestone:** M3 Guardrails, costs, evals: ✅ **approved 2026-09-27**, committed `M3: Guardrails, costs, evals` and pushed. Next: **M4 Ship** (not planned yet).
- **Live:** repo https://github.com/seripavan7-alt/lucentpad · site https://seripavan7-alt.github.io/lucentpad/
- **Next action:** draft the M4 plan (`docs/handoff/M4.md`: hosted GCP demo, README, usage docs, video, load test) and wait for the user's "go".
- **Blockers:** none. Live eval check on GitHub needs an `ANTHROPIC_API_KEY` repo secret (optional; the workflow skips without it).

## M3 contract notes
- Engine API (stub): `sdk/lucentpad/guardrails/__init__.py`: `redact(text)->Redacted`, `parse_rules(data)->list[Rule]`,
  `check_prompt(rules,text)->Block|None`, `check_tool(rules,tool,args)->Block|None`. The server depends on
  `lucentpad-sdk` (workspace) to use it; Dockerfile copies `sdk/`. `pyyaml` in both.
- Routes: `GET /v1/guardrails/rules` (→ `app.state.guardrails: GuardrailProvider`, `server/lucentpad_server/guardrails/`),
  `GET /v1/guardrails/events?from&to&kind*&limit&cursor&since&hide_sample`, `GET /v1/guardrails/summary`,
  `GET /v1/pricing` (implemented; `PRICES_CHECKED`), `GET /v1/costs?from&to&group_by=model|client|service&hide_sample`,
  `POST /v1/evals/runs` (201), `GET /v1/evals/runs?suite&limit&cursor`, `GET /v1/evals/runs/{id}`. Store stubs → 501.
- Schema: `GuardrailRule(s)`, `GuardrailEvent(List)`, `GuardrailSummary`, `ModelPrice`, `PriceTable`, `CostPoint`,
  `CostSeries`, `EvalRunIn`/`EvalRun`/`EvalRunSummary`/`EvalRunList`, `EvalCaseResult`, `EvalCheckResult`.
  New Attr: `GUARDRAIL_REASON`, `BUDGET_SCOPE`, `EVAL_RUN_ID`, `EVAL_CASE`.

## M3 step log
| Step | State | Owner / date | Handoff notes |
| --- | --- | --- | --- |
| 0 Decisions D20–D28 | ✅ | lead 2026-09-27 | Defaults; live eval check deferred (no key). |
| 1 Contract | ✅ | lead 2026-09-27 | See **M3 contract notes**. |
| 2 Guardrail engine + redaction (`sdk/lucentpad/guardrails/`) | ✅ | sdk agent 2026-09-27 | Detectors: Anthropic/OpenAI/GitHub/AWS keys, Bearer tokens, emails, Luhn cards (first digit 2–6, card-like grouping, not adjacent to id chars). ~18–21 µs per 2 kB. Tool conditions: hand-written parser (comparisons, in/not in, and/or, parens, dotted fields); missing field never matches. |
| 3 Server: rules, pricing, ingest redaction, gateway enforcement, queries | ✅ | backend agent 2026-09-27 | `guardrails/{rules,redaction}.py`; env `LUCENTPAD_RULES_FILE` (YAML, reload on mtime; built-in `refund_limit` when unset), `LUCENTPAD_GATEWAY_SESSION_BUDGET_USD`. Ingest redaction in the writer thread (~9–49 µs/span). Gateway: prompt block → provider-shaped 400 + guardrail span; session budget alert once. Migration 0006: `guardrail_events` (filled by the writer, backfilled), `spans_cost_time_idx`, bucket statistics, `eval_runs`. Sample eval runs (8, one regressed) seeded; parity keys `guardrail_events`, `guardrail_summary`, `costs`, `eval_runs`; snapshot keys `eval_runs`, `guardrail_rules`. Lead did: broken rules file at startup → built-in rules stay active; types-PyYAML added. |
| 4 SDK: guardrails + budgets | ✅ | sdk agent 2026-09-27 | `GuardrailBlocked`, `BudgetExceeded`; `init(rules=, default_budget_usd=)`; `trace(budget_usd=, on_budget="alert"|"stop")`. Rules+prices fetched in a daemon thread (60 s; 5 s when down; keep last known on errors, clear on 404/501); first guardrail check waits ≤0.5 s once for rules. Redaction of all SDK strings at span end (captures +512 chars, cuts after redacting). Gaps: only last user message text checked; no AWS secret/Google/Slack/Stripe keys; parallel async calls can both pass a stop check. |
| 5 Demo agent + eval gate | ✅ | agent agent 2026-09-27 | Agent: catches `GuardrailBlocked` → handoff reply; `--budget USD`, `--local-rules` (rules.yaml = server built-in), tool previews carry args/results (email redacted by the SDK); new tool `reschedule_delivery`, order 1061. `lucentpad eval SUITE [--baseline] [--update-baseline] [--max-cost] [--endpoint] [--model] [--no-post] [--mock]` (exit 1 regression/cost > 1.25×/max-cost, 2 usage). `evals/support_agent.yaml` (6 cases, Haiku), mock `baseline.json`, `demo-break-prompt.patch` (→ 5 regressions), `.github/workflows/eval.yml` (skips with a notice without `ANTHROPIC_API_KEY`). `make eval`. |
| 6 Dashboard: Costs, Guardrails, Evals | ✅ | dashboard agent 2026-09-27 | Pages Costs (`?range`, `?group`), Guardrails (`?range`, `?kind`), Evals (+ `/evals/:runId`); hand-drawn SVG stacked columns per the dataviz skill (`--chart-1..6`, `--chart-other` tokens); PlaceholderPage removed. Adapter covers all M3 endpoints (32 parity cases). Gaps: server side closed by lead (budget alerts as guardrail events kind `budget`, migration 0007; `GuardrailSummary.budget_alerts`; `EvalRunSummary.baseline_cost_usd`; `hide_sample` on eval runs); dashboard side done (Costs budget alerts list, Guardrails Budget filter + tile, Evals cost vs baseline, hide_sample). |
| 7 Integration + checkpoint | ✅ | lead 2026-09-27 | Approved by the user 2026-09-27; home page roadmap marks M3 done, M4 next. Local stack (migrations 0005–0007): mock agent runs show step 3 (email never stored, `[REDACTED:email]`), step 4 (`refund_limit` block span, polite handoff), step 5 (budget alert with `--budget 0.001`); step 8 offline: break patch → 5 regressions, exit 1; runs visible on Evals. `make check` green (646 py + 330 web). Waiting: user's "approved"; live GitHub eval check deferred (no key). |

## M2 step log
| Step | State | Owner / date | Handoff notes |
| --- | --- | --- | --- |
| 0 Decisions D14–D19 | ✅ | lead 2026-09-25 | See decisions log. |
| 1 Contract | ✅ | lead 2026-09-25 | See **M2 contract notes**. |
| 2 Gateway proxy (`server/lucentpad_server/gateway/`) | ✅ | gateway agent 2026-09-25 | Modules config/clients/sessions/parse/spans/proxy. Env: `LUCENTPAD_{ANTHROPIC,OPENAI}_UPSTREAM`, `_GATEWAY_FAILOVER`, `_KEY_SALT` (random per process if unset), `_GATEWAY_SESSION_GAP` (1800), `_GATEWAY_SESSION_HEADERS` (x-claude-code-session-id), `_GATEWAY_READ_TIMEOUT` (600), `_GATEWAY_CAPTURE_CONTENT`, `_GATEWAY_CLIENT_UA`. accept-encoding→identity. Added p95 0.45 ms (in-process). UA rules for Copilot BYOK are unverified → check at checkpoint. Lead added: httpx runtime dep, dated-model pricing, cache tokens (`gen_ai.usage.cache_{read,creation}.input_tokens`, input_tokens = all input) in gateway + SDK + pricing. Gaps: agent-id/request-class headers not recorded; failover ignores Retry-After; HEAD /api/hello → 405; multi-instance needs shared sessions. |
| 3 Gateway query API (`store.py`, migrations) | ✅ | query agent 2026-09-25 | `gateway_turns`/`gateway_summary` in store.py; turn = span source=gateway kind=llm; client from the span attribute; order (start_time, span_id, trace_id) desc; per-client UNION ALL (PG16 can't use index order for `= ANY`). Migration `0004_gateway_turns.sql`: partial indexes `spans_gateway_{turns,client,stored}_idx`. Cursor base64url `{g,k,s,t,f}`. 7-day summary at 116k turns ≈ 0.5–0.75 s (fine at realistic volume; a generated client column would make it 159 ms). Parity keys `gateway_turns`, `gateway_summary`. Sample gateway spans lack upstream/ttfb/failover (to add). |
| 4 Gateway page (`dashboard/`) | ✅ | dashboard agent 2026-09-25 | `pages/GatewayPage.tsx`, `features/gateway/*`; URL `?range`, `?client`; totals tiles, session-grouped feed, setup empty state with copy buttons; 3 s `since` polling. Contract gap: no per-session totals (header sums only loaded turns). |
| 5 Docs + client setup | ✅ | lead 2026-09-25 | `docs/gateway.md`; getting-started §5 and home page tab updated. |
| 6 Checkpoint | ✅ | lead 2026-09-25 | Automated green (379 py + 227 web). Docker stack proxies real Anthropic/OpenAI (401 without key passed through verbatim, shown live as an Other turn). Waiting: user's live runs with their keys, then "approved". |

## M2 contract notes
- Proxy routes: `POST|GET /gateway/anthropic/{path}` and `/gateway/openai/{path}` → `app.state.gateway`
  (`GatewayProxy.forward(provider, path, request) -> Response`, `lucentpad_server/gateway/__init__.py`); 501 when unset.
- Query: `GET /v1/gateway/turns?from&to&client*&limit&cursor&since` → `GatewayTurnList{turns: GatewayTurn[], next_cursor, as_of}`
  (newest first; since+cursor → 422); `GET /v1/gateway/summary?from&to` → `GatewaySummary{clients: GatewayClientTotals[], as_of}`.
  Store methods `SpanStore.gateway_turns(...)` / `gateway_summary(...)` (stubs → 501).
- New `Attr`: `GATEWAY_UPSTREAM` (`lucentpad.gateway.upstream`), `TTFB_MS`, `KEY_FINGERPRINT`. `GatewayProvider` literal.

## M1 step log
States: ⬜ not started · 🟡 in progress (write who/when) · ⏸ paused (write where) · ✅ done.
Claim a step by setting it to 🟡 before starting. Fill the handoff notes when you finish.

| Step | State | Owner / date | Handoff notes |
| --- | --- | --- | --- |
| T1 Rename Prism → LucentPad | ✅ | lead 2026-09-25 | Case-aware replace everywhere; `server/lucentpad_server`; migration lock id is now `0x4C55_4345_4E54` ("LUCENT"). Local folder is still `~/projects/prism` (the user renames it). |
| T2 Logo (outline panes) | ✅ | lead 2026-09-25 | `components/Logo.tsx` (`LogoMark`, `Logo`), `public/favicon.svg`; sidebar + site header. |
| T3 Static demo mode (`dashboard/`) | ✅ | lead 2026-09-25 | `server/tests/test_demo_snapshot.py` writes/checks `dashboard/src/demo/{snapshot,parity}.json` (`make demo-snapshot`). `api/client.ts` `setFetcher`; `demo/adapter.ts` (cursor `d<index>`, fresh time shift); `demo/start.tsx`; `DemoModeContext` → banner + "Demo data" in `Layout`. **R1/step 2 contract changes must extend the adapter + `PARITY_QUERIES`.** |
| T4 Landing site + getting started | ✅ | lead 2026-09-25 | `vite build --mode site` (`npm run build:site`, `make site`) → `dist-site/`, base `/lucentpad/` (`LUCENTPAD_SITE_BASE` overrides), 404.html fallback. `src/site/` (Landing, GettingStarted renders `docs/getting-started.md`, SiteHeader). `main.tsx` branches on `MODE === "site"`; normal build has no site/snapshot code. `check-web` also builds the site. |
| T5 GitHub repo + Pages deploy | ✅ | lead 2026-09-25 | Repo https://github.com/seripavan7-alt/lucentpad (public). History: WIP squashed into M0, author `pavan seri <seripavan7@gmail.com>`; old history kept locally as branch `backup/pre-rewrite`. First green CI: https://github.com/seripavan7-alt/lucentpad/actions/runs/36168683052 . Pages: https://github.com/seripavan7-alt/lucentpad/actions/runs/36168683111 . git uses `gh auth git-credential` (global); `gh` active account must be `seripavan7-alt`. |
| T6 Verify | ✅ | lead 2026-09-25 | Local checks as above; live https://seripavan7-alt.github.io/lucentpad/ landing + `/demo/traces` deep link render (deep links answer HTTP 404 with the app, the GitHub Pages SPA fallback). |
| R0 Decisions D11–D13 | ✅ | lead 2026-09-25 | All recommendations accepted (see decisions log). |
| R1 Contract: time window, filters, facets | ✅ | lead 2026-09-25 | See **M1 contract notes** below. |
| R2 Backend filters + facets (`server/`) | ✅ | backend agent 2026-09-25 | Migration `0002_live_filters_previews.sql`: `spans.stored_at`, `traces.updated_at`, preview cols + `*_preview_key` ranking, `traces_models_idx` (GIN), `traces_updated_idx`, `lucentpad_meta` flags (`sample_seeded_at`, `real_data_at`, `sample_shifted_at`). Facets = one UNION ALL query. EXPLAIN (200k traces): windows use `traces_time_idx` (<0.1 ms); a no-match model filter without window walks the time index (24 ms). D11 shift runs each startup, locked vs first real insert; old volumes without the flag need `make down` once. Parity: 13 list + 5 facet queries added. |
| R3 Dashboard range picker + filter panel (`dashboard/`) | ✅ | dashboard agent 2026-09-25 | URL: `range` (15m default, omitted), `order=asc`, repeated `name/status/source/client/model/service`. Files: `features/traces/{view.ts,FilterPanel.tsx,TracesTable.tsx}`. Later pages reuse page 1's `from` (cursor fingerprint). Demo adapter covers the full contract (cursor `d12.<fp>`/`a12.<fp>`). |
| R4 Verify | ✅ | lead 2026-09-25 | Fresh `make dev`: sample shifted, 15m view non-empty, filters + counts, previews, sort. |
| 0 Decisions D1–D10 | ✅ | lead 2026-09-25 | All recommendations accepted; D2 prices in `pricing.py` (sample costs and the demo snapshot regenerated). |
| 1 Git and GitHub | ➡️ | | Done as T5. |
| 2 Contract and scaffolding | ✅ | lead 2026-09-25 | Contract in **M1 contract notes** below; `sdk/` + `examples/support_agent/` workspace members, SDK public API stubbed (`sdk/lucentpad/__init__.py`), `make agent Q=...`; sample.py uses `schema.Attr` keys. Sample previews (step 2.1) moved to the backend agent (R2/3). |
| 3 Ingest pipeline (`server/`) | ✅ | backend agent 2026-09-25 | `ingest/queue.py` (`InProcessSpanQueue`), `ingest/writer.py` (`QueuedIngest`: 1000 spans/100 ms, 4 retries 0.2→2 s then drop+count), `ingest/body_limit.py` (413). Env `LUCENTPAD_INGEST_QUEUE_MAX`=50000, `LUCENTPAD_INGEST_DRAIN_TIMEOUT`=5, `LUCENTPAD_INGEST_MAX_BODY_BYTES`=10 MiB (SDK: split on 413). Ingest→readable ≈125 ms. `since` = updated/stored after since−2 s. Gaps: list `since` capped at `limit` with no truncated flag; shutdown uses 429 not 503. Sample previews: 128 truncated spans. |
| 4 SDK (`sdk/`) | ✅ | sdk agent 2026-09-25 | API as stubbed **plus `lucentpad.set_attribute(key, value)`** on the current span. Constants copied in `sdk/lucentpad/_attrs.py` (test asserts == `schema.Attr`). Exporter: buffer 10k drop-oldest, every 250 ms / 100 spans, 2 s timeout, backoff ≤30 s on network/408/5xx, `Retry-After` on 429, split on 413, other 4xx dropped+counted; atexit ≤2 s. Env `LUCENTPAD_ENDPOINT`, `LUCENTPAD_DISABLED`. Gaps: `with_raw_response`/`with_streaming_response`/`with_options()` copies/`beta.messages`/Bedrock/Vertex untraced; never-iterated streams give no span; no cache-token attrs. |
| 5 Demo support agent (`examples/`) | ✅ | sdk agent 2026-09-25 | `examples/support_agent/support_agent/{config,tools,agent,mock_llm,__main__}.py`, `orders.json` (1042 Maya Patel $77 delivered; 1057 $489 for M3 guardrail). `make agent Q=...` live (needs `ANTHROPIC_API_KEY`) or add `--mock-llm [--mock-delay 0.6]`. Refund limit deliberately not in the system prompt (M3 guardrail blocks). Anthropic only (no OpenAI mode). |
| 6 Live dashboard (`dashboard/`) | ✅ | dashboard agent 2026-09-25 | `lib/usePolling.ts`; list 3 s (`since`=as_of, limit 200, full refetch if next_cursor), detail 1 s. Idle rule: live until root span arrives; no new spans for 10 s → not live, poll every 5 s. Hidden tab: no polling. Errors: interval doubles to 30 s. Tests run with `TZ=UTC`. |
| 7 Integration and checkpoint | ✅ | lead 2026-09-25 | `server/tests/test_e2e_live.py` (uvicorn + real SDK + mock agent: tree, ≤2 s live, cost per llm span, previews) green ×4. Fixes: API returns server-computed `lucentpad.cost_usd` on spans without one (`store._with_cost`); waterfall sibling sort keeps microseconds. Manual mock run in Chrome: live draw OK. Approved by the user 2026-09-25. |

## M1 contract notes (R1 + step 2; read before R2, R3, 3, 4, 5, 6)
- `GET /v1/traces` params: `from` (inclusive) / `to` (exclusive) on trace `start_time` (tz-aware ISO; naive → 422);
  repeatable `name`, `status`, `source`, `client`, `model` (matches any of `traces.models`), `service`
  (`service.name`): **OR within, AND across**; `limit` 1–200 (50); `cursor`; `order=desc|asc` (desc);
  `since` (live: traces with spans stored after it; not combinable with `cursor` → 422).
  Response `TraceList{traces, next_cursor, as_of}`. The cursor embeds order + `TraceFilter.fingerprint()`;
  reuse with another order/filter set → 422.
- `GET /v1/traces/facets` (same filter params) → `TraceFacets{name,status,source,client,model,service: [{value,count}]}`,
  each facet excluding its own filter, ≤ `FACET_MAX_VALUES` (50) per facet, count desc then value.
- `GET /v1/traces/{id}?since=` → `TraceDetail{trace, spans, as_of}`; with `since` only spans *stored* after it
  (needs an ingest timestamp column). `as_of` = DB `now()`; clients pass it back as `since`. The server applies a
  small overlap (e.g. 2 s) so nothing is missed; clients merge by id.
- `TraceSummary` gained `input_preview`, `output_preview` (nullable). Fold rule in handoff step 3.
- `POST /v1/spans`: 202 `{accepted}`; 429 + `Retry-After: 1` when the queue is full (all-or-nothing); 413 for big
  bodies; 501 until the lifespan puts an `IngestPipeline` (`lucentpad_server/ingest/__init__.py`) on
  `app.state.ingest`. `GET /v1/ingest/stats` → `IngestStats`.
- Shared query model: `lucentpad_server/query.py` (`TraceFilter`, `FACETS`). `SpanStore.list_traces(filters, *,
  limit, cursor, order, since) -> TraceList`, `facets(filters) -> TraceFacets`, `get_trace(id, *, since)`.
  Unimplemented parts raise `NotImplementedError` → 501 (placeholder until R2/3).
- New `schema` constants: `PREVIEW_MAX_CHARS = 2000`, `FACET_MAX_VALUES = 50`, `TraceOrder`; `Attr.INPUT_PREVIEW`,
  `OUTPUT_PREVIEW`, `INPUT_TRUNCATED`, `OUTPUT_TRUNCATED`, `FAILOVER_*`, `BUDGET_*`, `REDACTION_*`, `REFUND_AMOUNT`.
- Dashboard: `buildUrl` sends arrays as repeated params; `useTraces` still sends single source/status (R3 replaces it).
- SDK deps: `anthropic` 1.8 and `openai` 3.19 are built on **`httpx2`** (not `httpx`): provider mocks in SDK tests
  use `httpx2.MockTransport`; the exporter itself uses `httpx`. Test files outside `server/tests` have no
  `__init__.py` (pytest rootdir mode), so give them unique basenames (`test_sdk_*.py`, `test_agent_*.py`).

## Milestone history
### M2 Gateway: ✅ approved 2026-09-27, committed `M2: Gateway`
- Anthropic + OpenAI proxy routes, byte-identical streaming (~0.5 ms added p95), key fingerprints only,
  session grouping (Claude Code session header, else key + 30 min gap), non-streamed failover.
- Gateway query API (turns, per-client summary) + Gateway page (totals, session feed, live, setup cards).
- Cache-token pricing and dated model names. `docs/gateway.md`. Live Claude Code run verified.
### Post-M1 refinements: committed `UI refinements: sidebar filters, sorting, new home page`
- Filters in the app sidebar (closed by default, custom checkboxes), 24h default range, sort by any column,
  clock-following static demo, logo links home, new home page (receipt of a reschedule run, embedded
  dashboard, quick start + full guide, roadmap), sample data gains a `reschedule` support variant.

### M1 SDK and live waterfall: ✅ approved 2026-09-25, committed `M1: SDK and live waterfall`
- Rename to LucentPad, logo, landing site + static demo on GitHub Pages (commit `M1 prep`).
- Traces page: time range, facet filter panel, Input → Output, absolute times, sort, live list.
- Trace detail: live waterfall (1 s polling), cost per llm call, inspector previews.
- Ingest: bounded in-process queue + batch writer (~125 ms to readable), 429/413, stats.
- `lucentpad-sdk` (Anthropic + OpenAI, sync/async/streaming), demo support agent with `--mock-llm`.
- Real prices (checked 2026-09-25). E2E test: live within 2 s, cost per call. 198 py + 141 web tests.
### M0 Foundations: ✅ approved 2026-09-25, committed locally (`M0: Foundations`), not pushed
- Scaffold: uv workspace (Python 3.12), `Makefile`, `.gitignore`, `.dockerignore`, `CLAUDE.md`.
- Contract: `server/lucentpad_server/schema.py` (OTel GenAI attribute names, `Attr`/`EventName`), route
  signatures in `app.py`, exported to `contracts/openapi.json`; dashboard types generated from it.
- Backend: SQL migrations + runner (`db.py`), `SpanStore` (COPY inserts, pre-aggregated `traces` table),
  traces list/detail API, deterministic sample data (`sample.py`: 226 traces, ~1580 spans), illustrative
  `pricing.py`, Dockerfile. Ingest returns 501. 65 tests (testcontainers Postgres).
- Dashboard: Vite/React 19/TS, design tokens (light/dark/system), six-page shell, Traces list (URL filters,
  cursor paging), trace detail (static waterfall + span inspector), placeholders for Costs/Gateway/
  Guardrails/Evals. 66 tests.
- Infra: `docker-compose.yml` (db, api, dashboard); CI `.github/workflows/ci.yml` (`check` runs
  `make check`; `compose` boots the stack and checks it serves traces). **CI has never run** (no remote yet).
- Verified: `make check` green; `make dev` up on OrbStack; UI checked in Chrome, no console errors.

## Known follow-ups (not blocking)
- **Action item (user):** Copilot CLI + Copilot Chat live checks through the gateway (need an Anthropic/OpenAI key, or Ollama); verify Copilot BYOK User-Agent detection.
- **Later consideration (user):** tag Claude Code helper calls (title, quota) as "background" via `x-claude-code-request-class`; dim/hide on the Gateway page, still counted in cost.
- Sorting (post-M1): migration `0003_trace_sorts.sql` (`traces.duration_ms` generated column; indexes `traces_{duration,cost,name,source}_idx`). Cursor = base64url JSON `{s,k,id,o,f}` (cost key is a Decimal string); old cursors → 422. A very selective filter + non-started sort walks the sort index (1.7 ms at 200k). Demo adapter cursor `d12.cost.<fp>`. Dashboard keeps previous rows while a new sort/filter loads.
- While a run is live its trace is named after its first span (e.g. `chat claude-sonnet-5`) until the root span arrives last; fix idea: SDK exports a root "start" marker, or the server falls back to `service.name`.
- List `since` polls cap at `limit` with no truncated flag; ingest shutdown returns 429 not 503.
- Trace-summary `models` are sorted alphabetically, not by usage.
- Sample data is seeded once into the `pgdata` volume, so it ages; `make down` resets it.
- `pricing.py` prices only base input/output tokens; cache read/write pricing needs cache token counts on spans.
- `/healthz` doesn't check the DB.

## Decisions log
| Date | Decision |
| --- | --- |
| 2026-09-28 | Evals UI redesigned at the user's request ("when I click an eval I must know all cases belong to it"): two-pane view, runs list left (status stripe, case squares, selected run = accent card pointing at the panel), selected run panel right (accent border, header repeats suite/time/status/commit, case bar, tiles, expandable case cards: output, every check, baseline → now, trace link; failing cases open by default). `/evals` selects the newest run; `/evals/:runId` both routes to `EvalsPage`; narrow screens show list or run with "← All runs". Removed `EvalRunPage`, `RunsTable`, `CaseTable`. |
| 2026-09-27 | **M3 "go"** with D20–D28 as recommended in `docs/handoff/M3.md`. No Anthropic key yet: the eval gate is built and tested in mock mode; the live GitHub eval check (D25 secret, D28 PR) is an action item for when a key exists. |
| 2026-09-27 | Sample data is labelled (`spans.sample`/`traces.sample`, migration 0005 backfills existing DBs). `GET /v1/data` → `{sample_data, real_data}`; `hide_sample` param on traces, facets, gateway turns/summary. Dashboard: small "Sample" tag and a "Hide sample data" switch in the sidebar, both shown only when real data exists too (never in the static demo). |
| 2026-09-25 | M2 "go" with D16–D19 as recommended. |
| 2026-09-25 | M2 D14: user has **Anthropic + OpenAI** keys for the live checks (Claude Code + Copilot CLI on Anthropic, Copilot Chat Custom Endpoint on OpenAI). D15: Claude Code signs in with an **Anthropic API key**. Copilot is traceable only in BYOK mode (documented in M2.md). D16–D19: recommendations pending the user's "go". |
| 2026-09-25 | Landing page (user: "best of the 3 drafts, human, not vibecoded; don't wait for me"): `src/site/home/Home.tsx`: left-aligned hero ("See what your agent actually did.") with **Open the dashboard** + **Get started**; a receipt of one real demo run; C's dark "Open the dashboard. No install, no sign-up." stage with the **real dashboard embedded** (A); A's "Get started in minutes" tabbed quick start with the full guide expandable in place; "Built in the open" milestone list. Drafts removed. Demo links back to the site use `target=_top`. |
| 2026-09-25 | Traces list sortable by Name, Source, Duration, Cost (plus Started): `sort` param (`started|duration|name|source|cost`) + `order`; ties by trace_id; name/source code-point order (COLLATE "C"). |
| 2026-09-25 | Post-M1 UI refinements (user): Traces filters moved into the app sidebar under the nav (portal into `Layout`'s slot; inline sheet ≤720px), restyled like the nav; Status/Source open by default, 5 values + "Show N more". **Default range 24h** (was 15m, D13 amended). Static demo follows the viewer's clock on every request (`createLiveDemoFetch`): each range always shows the same traces, whenever and however long it's open. |
| 2026-09-25 | **M1 "go"** with every recommendation accepted: D11 (a) shift sample-only data on startup + "Show last 24 hours" empty state; D12 filters Name, Status, Source, Client, Model, Service with counts; D13 presets 15m/1h/4h/24h/7d/30d; D3 polling (detail 1 s, list 3 s, `since`); D4 SDK sets `stream_options.include_usage` and hides the usage chunk; D5 `lucentpad-sdk` / `import lucentpad`; D6 previews ≤2 000 chars + truncated flags, `capture_content=False` to disable; D8 absolute time + relative tooltip; D9 `Input → Output` column + inspector blocks; D10 time sort asc/desc in URL. |
| 2026-09-25 | D1: refund limit **$200**, budget **$0.50 per run** (config in M1, enforced in M3). |
| 2026-09-25 | D2: demo agent on Anthropic `claude-sonnet-5` → fallback `claude-haiku-4-5`; OpenAI pair `gpt-5` → `gpt-5-mini` also supported. Prices per MTok (in/out), checked 2026-09-25 on the official pages: sonnet-5 2/10, haiku-4-5 1/5, opus-5-5 4/20, gpt-5 1.25/10, gpt-5-mini 0.25/2. Cache pricing not modelled yet. Live key run: decided at the checkpoint (user runs it; mock mode otherwise). |
| 2026-09-25 | Repo public on GitHub. Git (author email, remote, push) deferred to M1 step 1 by the user. |
| 2026-09-25 | **Renamed Prism → LucentPad** (display `LucentPad`; code `lucentpad` everywhere: repo `seripavan7-alt/lucentpad`, `lucentpad-server`, SDK `lucentpad-sdk` / `import lucentpad`, CLI `lucentpad eval`, attrs `lucentpad.*`, env `LUCENTPAD_*`). Names checked free on PyPI/npm/GitHub. |
| 2026-09-25 | Logo: "outline panes" (two overlapping rounded-square outlines, indigo accent), wordmark Lucent + **Pad** in accent. Minimal; no ticks/heartbeats. |
| 2026-09-25 | Landing site for the open-source project with two central buttons: "How to set up and use" (on-site `/docs/getting-started`, rendered from `docs/getting-started.md`) and "Try the demo" (static in-browser demo). Rest of the landing page refined later. |
| 2026-09-25 | Demo: **static**, on GitHub Pages, sample-data snapshot served by an in-browser adapter (no backend). The M4 GCP hosted demo stays in scope for live ingest. |
| 2026-09-25 | License **Apache-2.0** (`LICENSE`, package metadata). Public history: squash "WIP: first attempt" into "M0: Foundations" before the first push. GitHub account: **`seripavan7-alt`** (new account; replaces `Pavan-Seri`). |
| 2026-09-25 | D7: git author email `seripavan7@gmail.com` (rewrite local history before the first push). |
| 2026-09-25 | Local dev via Docker (docker compose), OrbStack on the dev Mac; no Docker-free `make dev`. |
| 2026-09-25 | npm (not pnpm); plain SQL migrations + asyncpg (not Alembic); one `server` package (gateway/CLI join later); `sdk/` separate from M1. |
| 2026-09-25 | Failover and budget alerts are span *events*; guardrail blocks are their own span (`kind=guardrail`). |
| 2026-09-25 | Dashboard API types are generated from `contracts/openapi.json`; `make check` fails on drift. |
| 2026-09-25 | `spans` PK is `(trace_id, span_id)`; trace status = worst span (blocked > error > ok); malformed trace_id → 404; bad cursor → 422. |
| 2026-09-25 | M1 scope extended by user: absolute timestamps, input/output previews, time sort on the traces list (D8–D10 in `docs/handoff/M1.md`; recommendations pending confirmation). |
| 2026-09-25 | M0 refinements (15-min default view, time-range picker, left filter panel with name + other filters) added as M1 prerequisites R0–R4; not built. |
| 2026-09-25 | Work is split into handoff-ready steps (`docs/handoff/M<n>.md`) so any agent can resume any step. |
