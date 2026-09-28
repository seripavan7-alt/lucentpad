-- Budget alerts join the guardrail events table (kind 'budget'), so the Guardrails and Costs pages
-- can list them across traces. One row per `lucentpad.budget.alert` event (seq = its 1-based
-- position in `events`, time = the event's time).
ALTER TABLE guardrail_events DROP CONSTRAINT guardrail_events_kind_check;
ALTER TABLE guardrail_events
    ADD CONSTRAINT guardrail_events_kind_check CHECK (kind IN ('block', 'redaction', 'budget'));
ALTER TABLE guardrail_events ADD COLUMN budget_limit_usd double precision;
ALTER TABLE guardrail_events ADD COLUMN budget_spent_usd double precision;
ALTER TABLE guardrail_events ADD COLUMN budget_scope text;

INSERT INTO guardrail_events
    (trace_id, span_id, seq, kind, time, source, client, rule, reason, redaction_kind, count,
     sample, stored_at, budget_limit_usd, budget_spent_usd, budget_scope)
SELECT s.trace_id, s.span_id, e.seq::integer, 'budget', (e.ev ->> 'time')::timestamptz,
       s.source, s.attributes ->> 'lucentpad.client', NULL, NULL, NULL, 1, s.sample, s.stored_at,
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.budget.limit_usd') = 'number'
            THEN (e.ev -> 'attributes' ->> 'lucentpad.budget.limit_usd')::double precision END,
       CASE WHEN jsonb_typeof(e.ev -> 'attributes' -> 'lucentpad.budget.spent_usd') = 'number'
            THEN (e.ev -> 'attributes' ->> 'lucentpad.budget.spent_usd')::double precision END,
       left(e.ev -> 'attributes' ->> 'lucentpad.budget.scope', 20)
FROM spans AS s
CROSS JOIN LATERAL jsonb_array_elements(s.events) WITH ORDINALITY AS e(ev, seq)
WHERE s.events @> '[{"name": "lucentpad.budget.alert"}]'
  AND e.ev ->> 'name' = 'lucentpad.budget.alert'
ON CONFLICT DO NOTHING;
