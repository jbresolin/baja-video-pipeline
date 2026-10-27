-- Wave-2 integration (design §17).

-- Frames per second of the proxy, reported by the first holder with the
-- proxy upload (Frame-Rate header). NULL until then; the review UI then
-- assumes 30.
ALTER TABLE jobs ADD COLUMN frame_rate REAL;

-- Whose fault the failure that created the exclusion was: "worker" (this
-- worker could not do it) or "job" (bad input). A job fails once two
-- different workers have a "worker" exclusion for the same task.
-- Rows written before this migration were all treated as job failures, but
-- they cannot be told apart now; "worker" is the safe reading.
ALTER TABLE task_exclusions ADD COLUMN scope TEXT NOT NULL DEFAULT 'worker';

-- GET /events?worker_id=… also matches events a worker caused (actor).
CREATE INDEX events_by_actor ON events (actor, id);
CREATE INDEX events_by_time ON events (at, id);
