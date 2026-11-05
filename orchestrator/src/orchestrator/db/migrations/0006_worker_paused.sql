-- LIVESTACK-38 item 9: the volunteer can pause a worker from its tray icon.

-- 1 while the worker's last heartbeat said `paused: true`. A paused worker
-- is offered no task of any kind and is not chosen as second holder or as
-- the studio for direct pushes, but it still serves held raws to peers, so
-- it stays a download source (design §4). The fleet page shows "pausado".
ALTER TABLE workers ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
