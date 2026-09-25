# LucentPad status

**New agent? Read `CLAUDE.md` first, then this file, then `docs/handoff/M1.md`.**
Source of truth: `docs/PRD.md`. Process: plan → "go" → build → checkpoint → "approved" → commit `M<n>: <title>`.

## Now
- **Milestone:** M1 SDK and live waterfall. Part A0 (rename, logo, demo, landing site) got the user's
  "go" on 2026-09-25 and is being built; the rest of M1 is still waiting for its "go".
- **Next action:** the first A0 step (T1–T6 in `docs/handoff/M1.md`) that isn't ✅. After that: R0 (D11–D13), then R1–R4.
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
| T5 GitHub repo + Pages deploy | 🟡 | lead 2026-09-25 | `.github/workflows/pages.yml` written. Waiting on the user: confirm the author rewrite, a commit for A0, repo creation and push. |
| T6 Verify | 🟡 | lead 2026-09-25 | Local: `make check` green; `make down && make dev` boots (compose project pinned `name: lucentpad`); headless Chrome: landing, docs, demo list + detail, 400 px, dark. Remaining: the live Pages URL. |
| R0 Decisions D11–D13 | ⬜ | | |
| R1 Contract: time window, filters, facets | ⬜ | | |
| R2 Backend filters + facets (`server/`) | ⬜ | | |
| R3 Dashboard range picker + filter panel (`dashboard/`) | ⬜ | | |
| R4 Verify | ⬜ | | |
| 0 Decisions D1–D10 | ⬜ | | |
| 1 Git and GitHub | ➡️ | | Done as T5. |
| 2 Contract and scaffolding | ⬜ | | |
| 3 Ingest pipeline (`server/`) | ⬜ | | |
| 4 SDK (`sdk/`) | ⬜ | | |
| 5 Demo support agent (`examples/`) | ⬜ | | |
| 6 Live dashboard (`dashboard/`) | ⬜ | | |
| 7 Integration and checkpoint | ⬜ | | |

## Milestone history
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
- Trace-summary `models` are sorted alphabetically, not by usage.
- Sample data is seeded once into the `pgdata` volume, so it ages; `make down` resets it.
- `pricing.py` prices are placeholders (M1 step 0, D2).
- `/healthz` doesn't check the DB.

## Decisions log
| Date | Decision |
| --- | --- |
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
