-- LIVESTACK-42 item 6: the admin chooses, on the Settings page, whether
-- clips show the team's full or short name and the school's full name or
-- sigla. The job already keeps the full team name (team_name), the short
-- one (overlay_team_name) and the sigla (overlay_school_short); the full
-- school name was missing. Copied with them when the car number is set.
-- Jobs reviewed before this have none: the sigla is used for them.
ALTER TABLE jobs ADD COLUMN school_name TEXT;
