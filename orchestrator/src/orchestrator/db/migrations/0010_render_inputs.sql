-- LIVESTACK-43: edits after delivery are visible and reprocessable.
--
-- A clip version remembers what it was rendered from (cuts, review
-- fields, the Settings' name choices and the names the overlay shows, the
-- overlay theme version, the raw's sha256), as shared.models.RenderInputs
-- JSON, so the review UI can say when the clip on air no longer matches
-- the review. NULL for versions rendered before this: nothing is known.
ALTER TABLE clip_versions ADD COLUMN render_inputs_json TEXT;

-- What the orchestrator handed out with the current process claim
-- (RenderInputs JSON, reported_by_worker false). It becomes the version's
-- inputs when the rendering worker reports nothing (older workers).
-- Replaced by every process claim.
ALTER TABLE jobs ADD COLUMN claimed_render_inputs_json TEXT;

-- What the rendering worker reported (PUT /jobs/{id}/render-inputs,
-- RenderInputsReport JSON, with the clip's sha256) before uploading or
-- pushing the clip. Used when that clip becomes a version; a report for
-- another clip is ignored.
ALTER TABLE jobs ADD COLUMN reported_render_inputs_json TEXT;
