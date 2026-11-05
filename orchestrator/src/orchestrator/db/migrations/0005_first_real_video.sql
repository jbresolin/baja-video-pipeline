-- Fixes and additions after the first real video (LIVESTACK-38).

-- The raw's ffprobe report (shared.models.ProbeReport as JSON, location
-- tags already removed), sent by the first holder with the proxy
-- (PUT /jobs/{id}/media-info). NULL until then.
ALTER TABLE jobs ADD COLUMN media_info_json TEXT;

-- While state is "discarded": the state it was discarded from
-- (awaiting_review, ready or failed), so "restore" can undo the discard,
-- and until when its raws are kept (discard time + the Settings page's discard_grace_hours,
-- fixed at discard time). Both NULL in every other state.
ALTER TABLE jobs ADD COLUMN discarded_from TEXT;
ALTER TABLE jobs ADD COLUMN discard_grace_ends_at TEXT;
-- Jobs discarded before this migration had their raws deleted already:
-- their grace period is over.
UPDATE jobs SET discard_grace_ends_at = COALESCE(finished_at, updated_at)
 WHERE state = 'discarded';

-- Every rendering of every job's clip (design §11 as amended: processed
-- clips are the archive, and reprocessed clips are versions, not
-- replacements). A new row when a clip with a new sha256 reaches the
-- outbox or the studio; jobs.clip_* keep describing the current one.
CREATE TABLE clip_versions (
    job_id         TEXT    NOT NULL REFERENCES jobs (id),
    -- 1, 2, ... in the order they were rendered.
    version        INTEGER NOT NULL,
    sha256         TEXT    NOT NULL,
    size_bytes     INTEGER NOT NULL,
    -- Box path at render time ("enduro/42/3.mp4").
    relative_path  TEXT    NOT NULL,
    rendered_by    TEXT,
    rendered_at    TEXT    NOT NULL,
    -- When the master confirmed its archive copy (POST …/clip-archived).
    archived_at    TEXT,
    -- Last time the master reported archive_clip failed for this job; the
    -- version is not offered again for a few minutes, so one broken clip
    -- cannot hold up the others.
    archive_failed_at TEXT,
    PRIMARY KEY (job_id, version),
    UNIQUE (job_id, sha256)
);
CREATE INDEX clip_versions_unarchived ON clip_versions (archived_at, rendered_at);

-- Finished clips each worker reports keeping (HeartbeatRequest.held_clips),
-- replaced on every heartbeat. Sources for archive_clip; fleet counts.
CREATE TABLE worker_clips (
    worker_id  TEXT NOT NULL REFERENCES workers (id),
    job_id     TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    PRIMARY KEY (worker_id, sha256)
);
CREATE INDEX worker_clips_by_sha ON worker_clips (sha256);

-- Clips rendered before this migration become version 1.
INSERT INTO clip_versions (job_id, version, sha256, size_bytes, relative_path, rendered_by, rendered_at)
SELECT id, 1, clip_sha256, clip_size_bytes, clip_relative_path, clip_rendered_by, updated_at
  FROM jobs WHERE clip_sha256 IS NOT NULL;
