-- 0001: spans (one row per span) and traces (one pre-aggregated row per trace).
--
-- spans keeps the full OTel-shaped span (attributes/events as jsonb) plus typed columns
-- extracted from well-known attributes (model, tokens, cost) so aggregates stay cheap.
-- traces is maintained by the writer in the same statement that inserts spans (see
-- store.py), so listing traces never aggregates over spans at read time.
-- Neither table has foreign keys or triggers: that keeps bulk COPY-into-staging inserts
-- cheap and lets child spans arrive before their root (the SDK exports the root last).

CREATE FUNCTION lucentpad_status_rank(s text) RETURNS integer
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT CASE s WHEN 'blocked' THEN 2 WHEN 'error' THEN 1 ELSE 0 END $$;

CREATE TABLE spans (
    trace_id       text        NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
    span_id        text        NOT NULL CHECK (span_id ~ '^[0-9a-f]{16}$'),
    parent_span_id text                 CHECK (parent_span_id ~ '^[0-9a-f]{16}$'),
    name           text        NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('agent', 'llm', 'tool', 'guardrail')),
    source         text        NOT NULL CHECK (source IN ('sdk', 'gateway')),
    start_time     timestamptz NOT NULL,
    end_time       timestamptz NOT NULL,
    status         text        NOT NULL CHECK (status IN ('ok', 'error', 'blocked')),
    status_message text,
    model          text,
    input_tokens   bigint,
    output_tokens  bigint,
    cost_usd       numeric(18, 8),
    attributes     jsonb       NOT NULL DEFAULT '{}',
    events         jsonb       NOT NULL DEFAULT '[]',
    -- (trace_id, span_id) rather than span_id alone: W3C span IDs are only meant to be
    -- unique within a trace, and this index also serves "fetch every span of a trace".
    PRIMARY KEY (trace_id, span_id)
);

CREATE TABLE traces (
    trace_id      text           PRIMARY KEY,
    has_root      boolean        NOT NULL,  -- false until the root span has arrived
    name          text           NOT NULL,  -- root span's (earliest span's until then)
    source        text           NOT NULL,
    service_name  text,
    client        text,
    start_time    timestamptz    NOT NULL,  -- min(span.start_time)
    end_time      timestamptz    NOT NULL,  -- max(span.end_time)
    status        text           NOT NULL,  -- worst span status: blocked > error > ok
    span_count    integer        NOT NULL,
    llm_calls     integer        NOT NULL,
    input_tokens  bigint         NOT NULL,
    output_tokens bigint         NOT NULL,
    cost_usd      numeric(18, 8) NOT NULL,
    models        text[]         NOT NULL   -- distinct, sorted
);

-- Keyset pagination: ORDER BY start_time DESC, trace_id DESC, optionally filtered.
CREATE INDEX traces_time_idx ON traces (start_time DESC, trace_id DESC);
CREATE INDEX traces_source_time_idx ON traces (source, start_time DESC, trace_id DESC);
CREATE INDEX traces_status_time_idx ON traces (status, start_time DESC, trace_id DESC);
