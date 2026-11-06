-- LIVESTACK-41: worker polish from real use.

-- Item 4: 1 while the worker's last heartbeat said `shared_tailnet: true`:
-- the volunteer chose "Usar a rede atual" (the PC stays on its own
-- tailnet, into which the VPS is shared). It reaches the orchestrator, but
-- other workers and the studio cannot reach it. So it is never chosen as
-- second holder, never offered to peers as a download source, and its
-- clips go to the outbox, not straight to the studio. The fleet page shows
-- "rede compartilhada".
ALTER TABLE workers ADD COLUMN shared_tailnet INTEGER NOT NULL DEFAULT 0;

-- Item 12: the raw's readable name on workers' disks, e.g.
-- "joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4" (sender, time the
-- bot received it, start of the job id; shared.layout.raw_file_name). Fixed
-- at intake, so renaming the contact later renames nothing. NULL for jobs
-- from before: workers then keep "<job_id>.<ext>". The VPS inbox always
-- keeps "<job_id>.<ext>".
ALTER TABLE jobs ADD COLUMN raw_file_name TEXT;
