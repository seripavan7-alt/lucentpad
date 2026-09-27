-- Label the startup sample data, so the dashboard can tag it and hide it once real data exists.
ALTER TABLE spans ADD COLUMN sample boolean NOT NULL DEFAULT false;
ALTER TABLE traces ADD COLUMN sample boolean NOT NULL DEFAULT false;

-- Existing databases: the sample was seeded in one transaction that also wrote the
-- sample_seeded_at flag, so its spans share that transaction's timestamp.
UPDATE spans SET sample = true
WHERE stored_at <= (SELECT updated_at FROM lucentpad_meta WHERE key = 'sample_seeded_at');
UPDATE traces SET sample = true
WHERE trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE sample);
