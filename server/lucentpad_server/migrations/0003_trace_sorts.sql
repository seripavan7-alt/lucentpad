-- 0003: sort the traces list by duration, name, source or cost (keyset pagination for each).

-- Trace duration in milliseconds, kept in step with start_time/end_time by Postgres (the
-- writer's merge and the D11 sample shift only touch those two columns).
ALTER TABLE traces
    ADD COLUMN duration_ms double precision
        GENERATED ALWAYS AS ((EXTRACT(EPOCH FROM (end_time - start_time)) * 1000)::double precision)
        STORED;

-- One (key, trace_id) index per sort; a btree scans either way, so both orders use it.
-- name/source sort in byte order (COLLATE "C") so results are reproducible across locales.
CREATE INDEX traces_duration_idx ON traces (duration_ms, trace_id);
CREATE INDEX traces_cost_idx ON traces (cost_usd, trace_id);
CREATE INDEX traces_name_idx ON traces ((name COLLATE "C"), trace_id);
CREATE INDEX traces_source_idx ON traces ((source COLLATE "C"), trace_id);
