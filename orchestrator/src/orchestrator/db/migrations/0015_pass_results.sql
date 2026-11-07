-- LIVESTACK-47: official pass results in the overlay.
--
-- The last answer of the results API for each configured prova's table
-- (resultado.php?evt=…&id=…&display=1), and how fetching it has gone. A
-- failed fetch keeps the last good answer (body_json), so nothing on air
-- is blanked by a failure (domain/pass_results_fetch.py).
CREATE TABLE pass_result_tables (
    -- The results API's resultado id, event included: "26BR_MAN".
    resultado_id   TEXT PRIMARY KEY,
    -- The last good answer, as JSON; NULL until a fetch worked.
    body_json      TEXT,
    -- When body_json was fetched.
    success_at     TEXT,
    -- The last request, whether it worked, and its error.
    attempt_at     TEXT,
    attempt_ok     INTEGER,
    error          TEXT,
    -- The first failure after the last success; NULL while it works.
    failing_since  TEXT,
    -- The answer's Cache-Control max-age (5 s while the event runs, 300 s
    -- once finished): the next fetch waits at least 30 s and at least this.
    max_age_s      INTEGER
);

-- "Mostrar resultado da passagem" on the job page. NULL = never set by a
-- reviewer: the prova's show_by_default applies.
ALTER TABLE jobs ADD COLUMN show_pass_result INTEGER;

-- The pass-results configuration itself is a text in app_settings
-- (key "pass_results_config"); no table needed.
