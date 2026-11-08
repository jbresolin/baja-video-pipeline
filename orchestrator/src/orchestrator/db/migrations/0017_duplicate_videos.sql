-- LIVESTACK-51 item 3: the same video sent twice, or forwarded by someone
-- else, is a duplicate (domain/duplicates.py).
--
-- A duplicate is a job like any other, discarded at once by intake with
-- the reason "duplicate of <first job>", so it can be restored. This is
-- the first job: the oldest one with the same raw SHA-256 that was not
-- discarded when the copy arrived. NULL for every other job, and for
-- every job from before.
ALTER TABLE jobs ADD COLUMN duplicate_of TEXT REFERENCES jobs (id);

-- Intake looks up every new raw's SHA-256, and the first job's page lists
-- its duplicates.
CREATE INDEX jobs_by_sha256 ON jobs (sha256);
CREATE INDEX jobs_by_duplicate_of ON jobs (duplicate_of) WHERE duplicate_of IS NOT NULL;
