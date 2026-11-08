-- LIVESTACK-52: watch and download the rendered clip.

-- "Ver clipe": the worker that rendered a clip uploads a 480p preview of it
-- to previews/<job_id>.<clip sha256>.mp4. This is the SHA-256 of the clip
-- that preview shows (NULL = none ever arrived). The column stays when the
-- file is evicted, so the page can say "evicted" rather than "missing";
-- a preview of an older version than the newest counts as none.
ALTER TABLE jobs ADD COLUMN preview_clip_sha256 TEXT;

-- Whether the last process task handed out carried a preview_upload_url,
-- i.e. "Gerar prévia dos clipes" was on when the clip was claimed: 1 or 0.
-- NULL for clips rendered before this change (they have no preview).
ALTER TABLE jobs ADD COLUMN preview_wanted INTEGER;

-- "Baixar clipe": when a reviewer last asked for the clip (NULL = never or
-- the copy was deleted) and which version they got (its SHA-256: always the
-- newest at the time of the request). While asked, a PC that has the clip
-- is offered upload_clip until requested_clips/<job_id>.mp4 exists.
ALTER TABLE jobs ADD COLUMN clip_download_requested_at TEXT;
ALTER TABLE jobs ADD COLUMN clip_download_sha256 TEXT;

-- When the first complete download of that copy ended (NULL = not yet).
-- The copy is deleted clip_download_keep_minutes (Settings, 30) later.
ALTER TABLE jobs ADD COLUMN clip_download_completed_at TEXT;
