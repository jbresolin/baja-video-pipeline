-- LIVESTACK-67: the top speed under the clock ("Aceleração e Velocidade").
--
-- Where the car crosses the speed trap, as the reviewer marks it on the job
-- page ("Velocidade", next to "Largada"), in raw-video seconds like the
-- largada and the cuts. From that frame to the end of the clip a second
-- pill under the clock shows the run's official speed. NULL = not marked,
-- so no speed pill, which is every job from before this change.
ALTER TABLE jobs ADD COLUMN speed_mark_s REAL;
