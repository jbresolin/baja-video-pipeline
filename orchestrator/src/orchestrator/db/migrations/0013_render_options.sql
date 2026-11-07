-- LIVESTACK-50: render options, vertical videos and no audio.
--
-- The raw's picture as players show it, after the file's own rotation
-- note (1080 x 1920 for a phone filmed upright), read from the media-info
-- report when it arrives. NULL until then. Jobs from before get it at the
-- next start, once, from the report already stored
-- (domain/first_start.py backfill_display_size), with no bot reply.
ALTER TABLE jobs ADD COLUMN display_width INTEGER;
ALTER TABLE jobs ADD COLUMN display_height INTEGER;

-- The reviewer's render options, saved with the review fields
-- (shared.models.Job). The defaults are what every job had before:
-- no turn, blurred sides (only used when the picture is not 16:9), the
-- crop band in the middle, the sound kept.
ALTER TABLE jobs ADD COLUMN rotation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN framing TEXT NOT NULL DEFAULT 'blurred';
ALTER TABLE jobs ADD COLUMN crop_position REAL NOT NULL DEFAULT 0.5;
ALTER TABLE jobs ADD COLUMN no_audio INTEGER NOT NULL DEFAULT 0;

-- Workers declare their render features inside capabilities_json
-- (WorkerCapabilities.render_features); no column needed. Rows from
-- before simply lack the key, which reads as "none".
