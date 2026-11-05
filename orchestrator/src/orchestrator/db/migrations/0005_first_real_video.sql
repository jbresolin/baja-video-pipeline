-- Fixes and additions after the first real video (LIVESTACK-38).

-- The raw's ffprobe report (shared.models.ProbeReport as JSON, location
-- tags already removed), sent by the first holder with the proxy
-- (PUT /jobs/{id}/media-info). NULL until then.
ALTER TABLE jobs ADD COLUMN media_info_json TEXT;
