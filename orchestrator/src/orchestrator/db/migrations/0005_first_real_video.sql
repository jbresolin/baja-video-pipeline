-- Fixes and additions after the first real video (LIVESTACK-38).

-- The raw's ffprobe report (shared.models.ProbeReport as JSON, location
-- tags already removed), sent by the first holder with the proxy
-- (PUT /jobs/{id}/media-info). NULL until then.
ALTER TABLE jobs ADD COLUMN media_info_json TEXT;

-- While state is "discarded": the state it was discarded from
-- (awaiting_review, ready or failed), so "restore" can undo the discard,
-- and until when its raws are kept (discard time + BVP_DISCARD_GRACE_HOURS,
-- fixed at discard time). Both NULL in every other state.
ALTER TABLE jobs ADD COLUMN discarded_from TEXT;
ALTER TABLE jobs ADD COLUMN discard_grace_ends_at TEXT;
-- Jobs discarded before this migration had their raws deleted already:
-- their grace period is over.
UPDATE jobs SET discard_grace_ends_at = COALESCE(finished_at, updated_at)
 WHERE state = 'discarded';
