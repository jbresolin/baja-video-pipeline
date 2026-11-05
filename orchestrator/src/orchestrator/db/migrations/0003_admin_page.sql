-- Admin page (LIVESTACK-37): logins, contacts, provas, teams and settings
-- move from files on the VPS into the database.

-- People who sign in to the review UI. The bcrypt hash is the same kind
-- Caddy used, so reviewers.txt lines import as they are.
CREATE TABLE users (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT    NOT NULL,
    -- reviewer | admin
    role           TEXT    NOT NULL,
    disabled       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

-- Signed-in browsers. Only the SHA-256 of the session token is stored.
-- A session ends 12 h after last_seen_at (no activity) or at logout.
CREATE TABLE sessions (
    token_sha256  TEXT PRIMARY KEY,
    username      TEXT NOT NULL REFERENCES users (username),
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (username);

-- The list of provas a reviewer picks from. `slug` is the clip folder
-- name; it is fixed when the prova is created and never changes, so a
-- rename does not move clips to a new folder.
CREATE TABLE provas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL UNIQUE,
    slug           TEXT    NOT NULL UNIQUE,
    display_order  INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

-- Everyone who wrote to the bot, recorded on first contact.
CREATE TABLE contacts (
    -- Digits only, from the webhook's remoteJid ("5511987654321").
    phone                  TEXT PRIMARY KEY,
    display_name           TEXT,
    -- The WhatsApp profile name from the newest message (pushName).
    push_name              TEXT,
    -- allowed | blocked | unknown
    status                 TEXT    NOT NULL DEFAULT 'unknown',
    default_prova_id       INTEGER REFERENCES provas (id),
    notes                  TEXT,
    first_seen_at          TEXT,
    last_seen_at           TEXT,
    -- When the "not authorized" reply was last sent (at most one per 24 h).
    last_refusal_reply_at  TEXT,
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);

-- Team names by car number (was BVP_TEAMS_FILE).
CREATE TABLE teams (
    car_number  INTEGER PRIMARY KEY,
    team_name   TEXT NOT NULL,
    school      TEXT,
    updated_at  TEXT NOT NULL
);

-- Operator settings edited in the UI. `value_json` is the JSON of the value.
CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One-time imports done at startup (teams file, prova backfill), so each
-- runs once even if the admin later empties the table.
CREATE TABLE first_start_imports (
    name     TEXT PRIMARY KEY,
    done_at  TEXT NOT NULL,
    detail   TEXT
);

-- Audit: an admin change records the value before and after, as JSON.
ALTER TABLE events ADD COLUMN details_json TEXT;
CREATE INDEX events_by_kind ON events (kind, id);

-- A revoked worker's bearer token no longer works.
ALTER TABLE workers ADD COLUMN revoked_at TEXT;

-- An admin can revoke an enrollment token that was not used yet.
ALTER TABLE enrollment_tokens ADD COLUMN revoked_at TEXT;
