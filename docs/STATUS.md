# LucentPad status

**New agent? Read `CLAUDE.md` first, then this file, then `docs/handoff/M1.md`.**
Source of truth: `docs/PRD.md`. Process: plan → "go" → build → checkpoint → "approved" → commit `M<n>: <title>`.

## Now
- **Milestone:** M1 ✅ approved 2026-09-25 and committed. **Next: M2 Gateway** (plan not written yet;
  write `docs/handoff/M2.md` and get the user's "go").
- **Live:** repo https://github.com/seripavan7-alt/lucentpad · site https://seripavan7-alt.github.io/lucentpad/
- **Next action:** plan M2 (PRD: Anthropic + OpenAI gateway routes with streaming passthrough, Gateway page,
  simple failover; Claude Code, Copilot Chat, Copilot CLI). M2 needs from the user: their Claude Code /
  Copilot setup. No Claude attribution in commits or PRs (user rule).
- **Blockers:** none.

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
