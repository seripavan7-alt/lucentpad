# LucentPad: rules for any agent working here

Read in this order before doing anything:
1. `docs/STATUS.md`: where the build is right now (milestone, step, blockers). **Start here.**
2. `docs/PRD.md`: the source of truth for what to build. If anything else disagrees with it, the PRD wins; ask the user.
3. `docs/handoff/M<n>.md` for the current milestone: the step-by-step plan, each step self-contained.

## Process (set by the user; do not skip)
- One milestone at a time: **plan → user says "go" → build → checkpoint → user says "approved" → commit `M<n>: <title>`.**
- Within a milestone, work one handoff step at a time. A step is done only when its acceptance checks pass
  and you have updated `docs/STATUS.md` (step log + next step). Assume the next step may be done by a
  different agent with no memory of this session.
- Run `make check` before calling anything done.
- Tests never call real LLM APIs. Never log, print, store or commit API keys.
- If a requirement is unclear or conflicts with the PRD, **ask the user. Don't guess.** Record the
  answer in the STATUS.md decisions log.
- Subagents: give each one a directory it exclusively owns. Never let two agents edit the same files.
  Shared files (contract, root config, Makefile, compose, CI, docs) are edited only by the lead agent.

## Repo map
| Path | What | Owner in parallel work |
| --- | --- | --- |
| `server/lucentpad_server/` | FastAPI ingest + query API, schema, store, sample data | backend |
| `server/lucentpad_server/schema.py`, `app.py` route signatures | **API contract** | lead only |
| `contracts/openapi.json` | exported contract; regenerate with `make contract` | lead only |
| `dashboard/` | React + TS + Vite dashboard; `src/api/schema.d.ts` is generated, never hand-edit | dashboard |
| `sdk/` (from M1) | `lucentpad-sdk` Python package | sdk |
| `examples/` (from M1) | demo support agent | agent |
| root files, `docker-compose.yml`, `.github/`, `docs/` | shared | lead only |

## Commands
- `make install`: uv sync + npm ci. `make dev`: docker compose stack, dashboard at http://localhost:5173.
- `make check`: contract drift, ruff, mypy (strict), pytest, eslint/prettier, tsc, vitest, vite build.
- `make contract`: after changing `schema.py` or route signatures; commit both generated files.
- `make down`: stop the stack and wipe its DB (the sample data reseeds on the next `make dev`).

## Conventions
- Python 3.12, uv workspace, `ruff` + `mypy --strict`. Pydantic models are frozen, `extra="forbid"`.
- Span attributes use OTel GenAI names (`gen_ai.*`) where they fit, otherwise `lucentpad.*`; add new keys as
  constants on `schema.Attr` / `schema.EventName`, not as string literals.
- Guardrail blocks are their own span (`kind="guardrail"`); failover and budget alerts are span events.
- DB tests use `LUCENTPAD_TEST_DATABASE_URL`, else testcontainers (the conftest finds OrbStack's socket).
- Dashboard: plain CSS on the tokens in `dashboard/src/styles/tokens.css`; no UI kit, no Tailwind.

## Dev machine notes
- Docker is OrbStack. Its CLI lives in `/Applications/OrbStack.app/Contents/MacOS/xbin` (on PATH via
  `~/.zprofile`; tools started without a login shell may need it prepended).
