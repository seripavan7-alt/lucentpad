-- 0004: Gateway page queries (M2). A turn is a spans row with source = 'gateway' AND kind = 'llm';
-- every index here is partial on exactly that predicate, so SDK traffic costs them nothing.

-- `GET /v1/gateway/turns`: newest first, keyset paging on (start_time, span_id, trace_id), and
-- the time window of `GET /v1/gateway/summary`.
CREATE INDEX spans_gateway_turns_idx ON spans (start_time DESC, span_id DESC, trace_id DESC)
    WHERE source = 'gateway' AND kind = 'llm';

-- Client filter (`?client=`, OR): the span's `lucentpad.client` attribute, then the page order.
CREATE INDEX spans_gateway_client_idx
    ON spans ((attributes ->> 'lucentpad.client'), start_time DESC, span_id DESC, trace_id DESC)
    WHERE source = 'gateway' AND kind = 'llm';

-- Live polling (`?since=`): turns stored after the previous poll.
CREATE INDEX spans_gateway_stored_idx ON spans (stored_at)
    WHERE source = 'gateway' AND kind = 'llm';
