-- Initial schema of the jobs DB (BVP_DATA_DIR/db/bvp.sqlite3).
--
-- Times are text in one fixed UTC format, "2026-10-25T14:03:07.000000Z",
-- so they sort and compare correctly as plain strings.
-- Booleans are 0/1 integers (SQLite has no boolean type).

-- One row per pass video (shared.models.Job). Holders and leases live in
-- their own tables because a job has several of each.
CREATE TABLE jobs (
    id                  TEXT PRIMARY KEY,
    state               TEXT    NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    -- When the job reached done or discarded; proxies of the oldest go first.
    finished_at         TEXT,

    -- Intake (shared.models.IntakeSource)
    message_id          TEXT    NOT NULL UNIQUE,
    sender_jid          TEXT,
    sender_name         TEXT,
    received_as         TEXT    NOT NULL,
    original_filename   TEXT,
    mimetype            TEXT,
    low_res             INTEGER NOT NULL DEFAULT 0,

    -- The raw
    sha256              TEXT    NOT NULL,
    size_bytes          INTEGER NOT NULL,
    -- ".mp4", ".mov" or ".bin": the inbox file is inbox/<id><raw_extension>.
    raw_extension       TEXT    NOT NULL,
    duration_s          REAL,
    has_audio           INTEGER,
    raw_on_vps          INTEGER NOT NULL DEFAULT 1,

    -- Proxy and thumbnail
    proxy_available     INTEGER NOT NULL DEFAULT 0,
    thumbnail_available INTEGER NOT NULL DEFAULT 0,
    -- A reviewer asked for the proxy to be rendered again (regenerate-proxy).
    proxy_requested     INTEGER NOT NULL DEFAULT 0,

    -- Review fields
    prova               TEXT,
    car_number          INTEGER,
    team_name           TEXT,
    passagem            INTEGER,
    show_pass           INTEGER NOT NULL DEFAULT 1,
    cut_in_s            REAL,
    cut_out_s           REAL,
    reviewed_by         TEXT,
    reviewed_at         TEXT,

    -- The finished clip (shared.models.ClipInfo); all NULL until rendered.
    clip_sha256         TEXT,
    clip_size_bytes     INTEGER,
    clip_relative_path  TEXT,
    clip_route          TEXT,
    clip_rendered_by    TEXT,
    clip_delivered_at   TEXT,

    -- Last worker failure (shared.models.JobFailure); all NULL when none.
    failure_worker_id   TEXT,
    failure_task_kind   TEXT,
    failure_message     TEXT,
    failure_exit_code   INTEGER,
    failure_at          TEXT,
    failed_from         TEXT,

    waiting_reason      TEXT
);
CREATE INDEX jobs_by_state ON jobs (state, created_at);

-- Every WhatsApp message the webhook accepted, including ones whose
-- download is still running or failed. Used to spot duplicate webhooks.
CREATE TABLE intake_messages (
    message_id  TEXT PRIMARY KEY,
    -- downloading | done | failed | rejected_inbox_full
    status      TEXT NOT NULL,
    job_id      TEXT,
    sender_jid  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    detail      TEXT
);

-- Machines running the worker app (shared.models.Worker).
CREATE TABLE workers (
    id                   TEXT PRIMARY KEY,
    name                 TEXT    NOT NULL,
    role                 TEXT    NOT NULL,
    hostname             TEXT,
    capabilities_json    TEXT    NOT NULL,
    -- SHA-256 of the bearer token; the token itself is never stored.
    bearer_token_sha256  TEXT    NOT NULL UNIQUE,
    free_bytes           INTEGER NOT NULL DEFAULT 0,
    storage_cap_bytes    INTEGER NOT NULL DEFAULT 0,
    tailnet_address      TEXT,
    file_server_port     INTEGER,
    receiver_port        INTEGER,
    enrolled_at          TEXT    NOT NULL,
    last_heartbeat       TEXT
);

-- One-time tokens a volunteer exchanges at POST /enroll.
CREATE TABLE enrollment_tokens (
    token_sha256       TEXT PRIMARY KEY,
    volunteer_name     TEXT NOT NULL,
    role               TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    used_at            TEXT,
    used_by_worker_id  TEXT,
    -- Tailscale's id of the auth key minted for it (not secret; for revocation).
    tailscale_key_id   TEXT
);

-- Workers that hold (or are downloading) a job's raw.
CREATE TABLE holders (
    job_id        TEXT NOT NULL REFERENCES jobs (id),
    worker_id     TEXT NOT NULL REFERENCES workers (id),
    -- assigned | confirmed
    status        TEXT NOT NULL,
    assigned_at   TEXT NOT NULL,
    confirmed_at  TEXT,
    PRIMARY KEY (job_id, worker_id)
);
CREATE INDEX holders_by_worker ON holders (worker_id);

-- Active leases only: a lease row is deleted when it ends or expires
-- (the events table keeps the history).
CREATE TABLE leases (
    lease_id     TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs (id),
    worker_id    TEXT NOT NULL REFERENCES workers (id),
    task_kind    TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX leases_by_job ON leases (job_id, task_kind);
CREATE INDEX leases_by_expiry ON leases (expires_at);

-- (job, worker, task) combinations not to offer again, after the worker
-- reported that task as failed for that job. Cleared by a review "retry".
CREATE TABLE task_exclusions (
    job_id      TEXT NOT NULL,
    worker_id   TEXT NOT NULL,
    task_kind   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (job_id, worker_id, task_kind)
);

-- Raw measurements from heartbeats; WorkerStats are rolling averages over
-- the newest few samples of each kind.
CREATE TABLE worker_stat_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id    TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    byte_count   INTEGER NOT NULL,
    seconds      REAL    NOT NULL,
    recorded_at  TEXT    NOT NULL
);
CREATE INDEX stat_samples_by_worker ON worker_stat_samples (worker_id, kind, id);

-- Append-only log: who did what to which job or worker, when and why.
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    job_id      TEXT,
    worker_id   TEXT,
    -- "system", "worker:<id>", "reviewer:<username>", "admin", "evolution"
    actor       TEXT NOT NULL,
    -- e.g. "state_changed", "holder_confirmed", "lease_expired"
    kind        TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT,
    reason      TEXT
);
CREATE INDEX events_by_job ON events (job_id, id);
CREATE INDEX events_by_worker ON events (worker_id, id);
