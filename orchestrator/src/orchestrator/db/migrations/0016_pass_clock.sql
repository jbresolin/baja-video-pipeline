-- LIVESTACK-48: the running clock in the overlay.
--
-- The largada the reviewer marks on the job page ("Largada"), in raw-video
-- seconds like the cuts; NULL = not marked, so no clock.
ALTER TABLE jobs ADD COLUMN largada_s REAL;

-- "Mostrar cronômetro" on the job page. NULL = never set by a reviewer:
-- the prova's show_by_default applies, as for show_pass_result.
ALTER TABLE jobs ADD COLUMN show_clock INTEGER;
