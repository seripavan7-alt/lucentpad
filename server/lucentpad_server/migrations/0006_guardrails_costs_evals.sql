-- 0006 (M3): guardrail events, cost series, eval runs.

-- One row per guardrail event, written by the span writer in the same statement that stores the
-- spans (store.py `_MERGE_STAGE`), so the Guardrails page reads a small indexed table instead of
-- unnesting every span's events. kind 'block': a kind='guardrail' span (seq 0, time = its
-- start_time). kind 'redaction': one `lucentpad.redaction` event of a span (seq = the event's
-- 1-based position in `events`, time = the event's time).
CREATE TABLE guardrail_events (
    trace_id       text        NOT NULL,
    span_id        text        NOT NULL,
    seq            integer     NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('block', 'redaction')),
    time           timestamptz NOT NULL,
    source         text        NOT NULL,
    client         text,
    rule           text,        -- blocks
    reason         text,        -- blocks
    redaction_kind text,        -- redactions
    count          integer     NOT NULL,
    sample         boolean     NOT NULL DEFAULT false,
    stored_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, span_id, seq)
);

-- `GET /v1/guardrails/events`: newest first, keyset on (time, trace_id, span_id, seq); the window
-- of `GET /v1/guardrails/summary`.
CREATE INDEX guardrail_events_time_idx
    ON guardrail_events (time DESC, trace_id DESC, span_id DESC, seq DESC);
-- `?kind=block` / `?kind=redaction`.
CREATE INDEX guardrail_events_kind_time_idx
    ON guardrail_events (kind, time DESC, trace_id DESC, span_id DESC, seq DESC);
-- Live polling (`?since=`).
CREATE INDEX guardrail_events_stored_idx ON guardrail_events (stored_at);

-- Backfill from spans already stored (the same projection as the writer's).
INSERT INTO guardrail_events
    (trace_id, span_id, seq, kind, time, source, client, rule, reason, redaction_kind, count,
     sample, stored_at)
SELECT trace_id, span_id, 0, 'block', start_time, source, attributes ->> 'lucentpad.client',
       attributes ->> 'lucentpad.guardrail.rule',
       coalesce(attributes ->> 'lucentpad.guardrail.reason', status_message),
       NULL, 1, sample, stored_at
FROM spans WHERE kind = 'guardrail'
UNION ALL
SELECT s.trace_id, s.span_id, e.seq::integer, 'redaction', (e.ev ->> 'time')::timestamptz,
       s.source, s.attributes ->> 'lucentpad.client', NULL, NULL,
       left(coalesce(e.ev -> 'attributes' ->> 'lucentpad.redaction.kind', 'unknown'), 100),
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.redaction.count') = 'number'
            THEN least(greatest((e.ev -> 'attributes' ->> 'lucentpad.redaction.count')::numeric,
                                0), 2147483647)::integer
            ELSE 1 END,
       s.sample, s.stored_at
FROM spans AS s
CROSS JOIN LATERAL jsonb_array_elements(s.events) WITH ORDINALITY AS e(ev, seq)
WHERE s.events @> '[{"name": "lucentpad.redaction"}]'
  AND e.ev ->> 'name' = 'lucentpad.redaction';

-- `GET /v1/costs`: spans with a cost in a time window. Covering for group_by=model (index-only
-- scan); client/service read the attribute from the heap.
CREATE INDEX spans_cost_time_idx ON spans (start_time)
    INCLUDE (cost_usd, model, input_tokens, output_tokens, sample)
    WHERE cost_usd IS NOT NULL;
-- The series groups by `date_bin('<n> seconds', start_time, epoch)`. Without statistics on that
-- expression the planner assumes one group per distinct start_time and sorts every row (to disk
-- at scale); with them it hash-aggregates (7-day window over 373k priced spans: ~0.5 s -> ~0.1 s).
-- The query inlines the same three literals (store.py `bucket_seconds_for`).
CREATE STATISTICS spans_cost_bucket_300_stats
    ON (date_bin('300 seconds'::interval, start_time, 'epoch'::timestamptz)) FROM spans;
CREATE STATISTICS spans_cost_bucket_3600_stats
    ON (date_bin('3600 seconds'::interval, start_time, 'epoch'::timestamptz)) FROM spans;
CREATE STATISTICS spans_cost_bucket_86400_stats
    ON (date_bin('86400 seconds'::interval, start_time, 'epoch'::timestamptz)) FROM spans;

-- Eval runs posted by `lucentpad eval`. Case results are kept whole as jsonb (always read with
-- their run); the pass/fail/regression counts are computed once on insert for the list.
CREATE TABLE eval_runs (
    id                text             PRIMARY KEY,
    suite             text             NOT NULL,
    status            text             NOT NULL
                          CHECK (status IN ('passed', 'failed', 'regressed', 'error')),
    started_at        timestamptz      NOT NULL,
    duration_ms       double precision NOT NULL,
    model             text,
    git_sha           text,
    git_ref           text,
    ci_url            text,
    cost_usd          double precision,
    baseline_cost_usd double precision,
    passed            integer          NOT NULL,
    failed            integer          NOT NULL,
    regressions       integer          NOT NULL,
    cases             jsonb            NOT NULL,
    sample            boolean          NOT NULL DEFAULT false,
    created_at        timestamptz      NOT NULL DEFAULT now()
);
CREATE INDEX eval_runs_time_idx ON eval_runs (started_at DESC, id DESC);
CREATE INDEX eval_runs_suite_time_idx ON eval_runs (suite, started_at DESC, id DESC);
