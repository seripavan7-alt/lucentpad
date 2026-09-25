# Prism status

**New agent? Read `CLAUDE.md` first, then this file, then `docs/handoff/M1.md`.**
Source of truth: `docs/PRD.md`. Process: plan → "go" → build → checkpoint → "approved" → commit `M<n>: <title>`.

## Now
- **Milestone:** M1 SDK and live waterfall. **Plan drafted, not yet approved** (needs the user's "go").
- **Next action:** Part A (prerequisites) in `docs/handoff/M1.md`: step R0, ask the user D11–D13; then
  R1–R4 (time range + filter panel on Traces). Step 0 (D1–D10) can be asked at the same time.
- **Blockers:** none besides the step 0 answers.

## M1 step log
States: ⬜ not started · 🟡 in progress (write who/when) · ⏸ paused (write where) · ✅ done.
Claim a step by setting it to 🟡 before starting. Fill the handoff notes when you finish.

| Step | State | Owner / date | Handoff notes |
| --- | --- | --- | --- |
| R0 Decisions D11–D13 | ⬜ | | |
| R1 Contract: time window, filters, facets | ⬜ | | |
| R2 Backend filters + facets (`server/`) | ⬜ | | |
| R3 Dashboard range picker + filter panel (`dashboard/`) | ⬜ | | |
| R4 Verify | ⬜ | | |
| 0 Decisions D1–D10 | ⬜ | | |
| 1 Git and GitHub | ⬜ | | |
| 2 Contract and scaffolding | ⬜ | | |
| 3 Ingest pipeline (`server/`) | ⬜ | | |
| 4 SDK (`sdk/`) | ⬜ | | |
| 5 Demo support agent (`examples/`) | ⬜ | | |
| 6 Live dashboard (`dashboard/`) | ⬜ | | |
| 7 Integration and checkpoint | ⬜ | | |

## Milestone history
### M0 Foundations: ✅ approved 2026-09-25, committed locally (`M0: Foundations`), not pushed
- Scaffold: uv workspace (Python 3.12), `Makefile`, `.gitignore`, `.dockerignore`, `CLAUDE.md`.
- Contract: `server/prism_server/schema.py` (OTel GenAI attribute names, `Attr`/`EventName`), route
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
| 2026-09-25 | Repo name `prism`, public on GitHub. Git (author email, remote, push) deferred to M1 step 1 by the user. |
| 2026-09-25 | Local dev via Docker (docker compose), OrbStack on the dev Mac; no Docker-free `make dev`. |
| 2026-09-25 | npm (not pnpm); plain SQL migrations + asyncpg (not Alembic); one `server` package (gateway/CLI join later); `sdk/` separate from M1. |
| 2026-09-25 | Failover and budget alerts are span *events*; guardrail blocks are their own span (`kind=guardrail`). |
| 2026-09-25 | Dashboard API types are generated from `contracts/openapi.json`; `make check` fails on drift. |
| 2026-09-25 | `spans` PK is `(trace_id, span_id)`; trace status = worst span (blocked > error > ok); malformed trace_id → 404; bad cursor → 422. |
| 2026-09-25 | M1 scope extended by user: absolute timestamps, input/output previews, time sort on the traces list (D8–D10 in `docs/handoff/M1.md`; recommendations pending confirmation). |
| 2026-09-25 | M0 refinements (15-min default view, time-range picker, left filter panel with name + other filters) added as M1 prerequisites R0–R4; not built. |
| 2026-09-25 | Work is split into handoff-ready steps (`docs/handoff/M<n>.md`) so any agent can resume any step. |
