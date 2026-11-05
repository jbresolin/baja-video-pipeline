-- The school's short name ("UFMG"), for the clip overlay's role pill
-- (LIVESTACK-38 item 8). The results API sends it as `school_short`; like
-- the other team columns, override_school_short is an admin's correction
-- of a synced team that later syncs keep.
ALTER TABLE teams ADD COLUMN school_short TEXT;
ALTER TABLE teams ADD COLUMN override_school_short TEXT;

-- Copied onto the job with overlay_team_name when the car number is set,
-- so a rendering uses the names the reviewer saw.
ALTER TABLE jobs ADD COLUMN overlay_school_short TEXT;
