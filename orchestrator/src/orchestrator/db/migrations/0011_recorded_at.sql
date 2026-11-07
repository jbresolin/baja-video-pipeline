-- LIVESTACK-45: help reviewers number the passagens.
--
-- When each video was filmed, so the review queue and the "other videos
-- of this car" panel can list them in filming order (arrival order is not
-- filming order). domain/recording_time.py has the rules.

-- When the filmmaker sent it (WhatsApp messageTimestamp) and when the bot
-- received the webhook. NULL sent_at = no timestamp (or a job from before).
ALTER TABLE jobs ADD COLUMN sent_at TEXT;
ALTER TABLE jobs ADD COLUMN received_at TEXT;

-- When it was filmed, and where that came from: 'metadata' (the raw's
-- creation_time, exact), 'whatsapp' (the send time) or 'received'.
ALTER TABLE jobs ADD COLUMN recorded_at TEXT;
ALTER TABLE jobs ADD COLUMN recorded_at_source TEXT;

-- Jobs from before: the job's creation is the closest thing to the
-- moment it was received. At startup, domain/first_start.py then reads
-- the creation_time of every probe report already stored, once.
UPDATE jobs SET received_at = created_at,
                recorded_at = created_at,
                recorded_at_source = 'received';

-- The review queue's order, and the panel's lookup by car.
CREATE INDEX jobs_by_recorded_at ON jobs (recorded_at, created_at, id);
CREATE INDEX jobs_by_car_number ON jobs (car_number);
