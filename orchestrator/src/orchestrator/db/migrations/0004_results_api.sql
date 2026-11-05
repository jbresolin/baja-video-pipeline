-- Brief amendment (2026-11-04): provas and teams come from the Baja results
-- API, per configured event (orchestrator/domain/results_sync.py).

-- Where a prova came from: 'manual' (typed on the admin page, or backfilled
-- from old jobs) or 'results_api'. A synced prova is identified by its API
-- prova id (e.g. "END"), the same across events.
ALTER TABLE provas ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE provas ADD COLUMN external_id TEXT;
-- 0 when the last sync did not list it. The sync then also set active = 0;
-- it never deletes the row, because jobs may use it.
ALTER TABLE provas ADD COLUMN in_source INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX provas_by_external_id ON provas (source, external_id);

-- Teams: team_name / short_name / school are the API's values for a synced
-- team (the admin's own for a manual one). An admin correction of a synced
-- team goes to the override_* columns, which the sync never touches.
ALTER TABLE teams ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE teams ADD COLUMN short_name TEXT;
ALTER TABLE teams ADD COLUMN in_source INTEGER NOT NULL DEFAULT 1;
ALTER TABLE teams ADD COLUMN override_team_name TEXT;
ALTER TABLE teams ADD COLUMN override_short_name TEXT;
ALTER TABLE teams ADD COLUMN override_school TEXT;

-- What the clip overlay shows for the team: the short name when the API has
-- one (Job.team_name keeps the full name for the review UI and latest.m3u).
ALTER TABLE jobs ADD COLUMN overlay_team_name TEXT;
