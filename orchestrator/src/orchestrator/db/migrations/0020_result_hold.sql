-- LIVESTACK-62: "Pronto" can wait for the official result before rendering.
--
-- While set, the job is `ready` but its render is offered to no worker: it
-- waits for the passagem's official result (and, with a running clock, the
-- official time) until this moment at the latest. Cleared when the result
-- arrives, the deadline passes, a reviewer clicks "Renderizar agora", or
-- the review changes so there is nothing to wait for. NULL = not held,
-- which is every job from before this change.
ALTER TABLE jobs ADD COLUMN result_hold_until TEXT;
