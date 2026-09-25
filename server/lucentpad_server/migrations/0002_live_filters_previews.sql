-- 0002: live polling timestamps, trace previews, filter indexes, sample-data bookkeeping.

-- Ingest time (not span time) of each row: `GET /v1/traces/{id}?since=` returns spans stored
-- after `since`, `GET /v1/traces?since=` traces updated after it. now() is the writer's
-- transaction time, so the clients' overlap (a couple of seconds) covers commit delay.
ALTER TABLE spans ADD COLUMN stored_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE traces ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- Previews (D9). *_key orders candidates: the root span always wins ('-infinity' for input,
-- 'infinity' for output); until it arrives, the earliest llm span's input and the latest llm
-- span's output (by start_time). NULL key = no candidate yet.
ALTER TABLE traces
    ADD COLUMN input_preview      text,
    ADD COLUMN input_preview_key  timestamptz,
    ADD COLUMN output_preview     text,
    ADD COLUMN output_preview_key timestamptz;

-- Model filter: `models && $1` (array overlap).
CREATE INDEX traces_models_idx ON traces USING gin (models);
-- Live list polling: traces updated after `since`.
CREATE INDEX traces_updated_idx ON traces (updated_at);

-- Key/value flags. 'sample_seeded_at': the sample data was loaded by the API at startup.
-- 'real_data_at': the first non-sample span was stored; from then on sample timestamps are
-- never shifted again (D11).
CREATE TABLE lucentpad_meta (
    key        text        PRIMARY KEY,
    value      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
