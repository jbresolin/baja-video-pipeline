# Worker app (`python -m worker`)

One program, three roles (design §13). The role comes from the enrollment
token, not from the config:

| Role | Does | Listens on |
| --- | --- | --- |
| `master` | holds **every** raw (design §4), makes proxies, renders clips. Raws it missed while offline are simply offered to it again (from a peer once the VPS copy is gone) | 8710 peer file server |
| `worker` | holds raws it is offered while under its storage cap, makes proxies, renders clips | 8710 peer file server |
| `studio` | never holds raws; receives pushed clips, pulls outbox clips (`deliver`), keeps `latest.m3u` | 8720 clip receiver |

All roles also serve the setup/status page and a small JSON API on
`http://127.0.0.1:8700` (localhost only); the Windows tray icon
(`BajaWorkerTray.exe`, `worker/tray/`) reads and drives the worker through
it. See "Status page, pause and the tray icon" below.

The contract it implements is [`docs/api.md`](../docs/api.md) (§5 worker
loop, §6 transfers, §7.2 worker-hosted endpoints, §8.1/8.3 folder layouts).

## Running it

```
uv run python -m worker --config path/to/worker.json [--console]
```

* No interactive input. Settings come from the config file; logs go to
  `<working folder>/logs/worker.log` (`--console` also prints them).
* Stops cleanly on CTRL-C, SIGTERM and (Windows) CTRL-BREAK, which is what
  NSSM sends a console program. Running tasks stop at their next check:
  downloads keep their partial file, ffmpeg is terminated, uploads stop
  between chunks, rendered clips stay in `clips/` for reuse. No visible
  file is ever half-written.
* The setup/status page on :8700 starts first, unconditionally, before
  anything else (LIVESTACK-41 item 1). Then `worker/lifecycle.py` walks
  through visible phases: a broken `worker.json` is shown and waited out
  (`worker/config_problems.py`); without a `worker_id` the page asks for
  the token and `POST /enroll` runs; a studio is asked for its clip
  folder; the worker waits until its saved tailnet address is on this PC
  (`worker/tailnet_wait.py`), saying why on the page; then the daemon
  runs. "Registrar de novo" goes back to the token form without leaving
  the process.

### Locally: the whole pipeline on one PC (five terminals)

No Caddy, no Tailscale, no WhatsApp. The orchestrator's dev server runs
with `BVP_DEV_MODE=1`, which makes it serve `/inbox/*`, `/outbox/*`,
`/proxies/*` and `/thumbs/*` itself, with the same permission rules as
Caddy + `GET /auth/file` and with Range support (design §18). Dev mode
refuses to start unless `BVP_PUBLIC_BASE_URL` is `localhost`/`127.0.0.1`,
so it cannot be left on by accident on the VPS. Needs `ffmpeg` on `PATH`.
All commands run from the repo root.

```sh
# Terminal 1: orchestrator (dev mode, fakes for Evolution/Tailscale/Baja app,
# data in .scratch/bvp-dev, admin token dev-admin, reviewer ana/ana)
uv run python -m orchestrator.testing.dev_server

# Terminal 2: mint three enrollment tokens and write three configs
# (.scratch/local/{master,worker,studio}/worker.json, non-clashing ports), then the master
uv run python orchestrator/fakes/make_local_workers.py
uv run python -m worker --config .scratch/local/master/worker.json --console

# Terminal 3: a volunteer worker
uv run python -m worker --config .scratch/local/worker/worker.json --console

# Terminal 4: the studio (the streaming PC's receiver)
uv run python -m worker --config .scratch/local/studio/worker.json --console

# Terminal 5: "a volunteer sends a video to the bot", as a WhatsApp document
uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4
uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4
```

Within a few seconds the master downloads the raw from the orchestrator's
`/inbox/…` and uploads a proxy. Still in terminal 5, review it (the
review UI works too, see `review_ui/README.md`; this is the curl version):

```sh
curl -s -u ana:ana 'http://127.0.0.1:8000/review/queue'        # note "id" and "version"
curl -s -u ana:ana -X PATCH -H 'Content-Type: application/json' \
  http://127.0.0.1:8000/jobs/<id>/review \
  -d '{"expected_version": <version>, "action": "ready", "prova": "Enduro",
       "car_number": 42, "passagem": 1, "cut_in_s": 1.0, "cut_out_s": 4.0}'
```

A holder renders the clip and pushes it to the studio:

```sh
find .scratch/local/studio/box -name '*.mp4'      # → .scratch/local/studio/box/enduro/42/1.mp4
cat .scratch/local/studio/box/latest.m3u
```

Stop the studio before marking a job ready to exercise the outbox path
instead (the studio pulls it from `/outbox/…` when it comes back). Start
over with `rm -rf .scratch/bvp-dev .scratch/local`.

Without the orchestrator, the test suite runs every role against an
in-memory fake (`tests/worker/fake_orchestrator.py`).

## Config file reference (`worker.json`)

Written by the installer and the setup page. Unknown keys are refused.

| Key | Default | Meaning |
| --- | --- | --- |
| `orchestrator_url` | `http://127.0.0.1:8000` | Caddy's tailnet listener, e.g. `http://100.101.102.103:8080`. Every call except `/enroll`. One of the two origins that get the bearer token. While it is still the default on an enrolled worker, the service uses `tailnet_base_url` instead (traffic only flows once the PC has joined the tailnet with `--first-run`), and the status page says "Conexão não concluída: abra 'Baja Worker - concluir conexão' no menu Iniciar." instead of the network error. |
| `enroll_url` | `null` = `orchestrator_url` | Public 443 site that routes `POST /enroll` (reachable before Tailscale is up). |
| `enrollment_token` | `null` | One-time token. Removed after a successful enrollment. |
| `worker_id`, `bearer_token`, `role` | `null` | Saved by enrollment. All three set = enrolled. Keep the file private: the token is the worker's identity. `role` is rewritten by the worker itself when an admin switches master and worker (`role_change.py`). |
| `tailnet_base_url` | `null` | Saved by enrollment from the `/enroll` answer: Caddy's tailnet listener that serves `/inbox/*` and `/outbox/*`. The second (and last) origin that gets the bearer token. `null` in configs enrolled before it existed: then only `orchestrator_url` gets the token. |
| `worker_name` | host name | The display name, sent at enrollment: the token form asks for it, prefilled with the host name (`display_name.py`). Afterwards an admin changes it on Admin → Workers; editing it here after enrollment changes nothing. |
| `working_folder` | the config file's folder | Where `raws/`, `clips/`, `tmp/`, `logs/` live. |
| `storage_cap_bytes` | 100 GiB | Upper limit for everything in the working folder. Volunteers stop taking new raws near it; held raws are never evicted. The master ignores it for raws (only real disk space stops it). |
| `ffmpeg_path` | `ffmpeg` | The bundled BtbN build on Windows. |
| `ffprobe_path` | `null` = next to `ffmpeg_path` | |
| `tailnet_address` | `null` = `tailscale ip -4` | Sent in heartbeats so peers and the studio can be reached. |
| `listen_address` | `null` = `tailnet_address`, else all interfaces | Address of the 8710/8720 servers. Binding to the Tailscale IP keeps raws off the local LAN. |
| `file_server_port` | 8710 | Peer file server (master, worker). `0` = any free port (tests); heartbeats report the port really bound. |
| `receiver_port` | 8720 | Clip receiver (studio). `0` as above. |
| `setup_page_port` | 8700 | Setup/status page and localhost JSON API on 127.0.0.1. |
| `paused` | `false` | Set by "Pausar" / cleared by "Retomar" (tray or status page). While true the worker finishes its current task and takes nothing new. Kept here so it survives a reboot. |
| `studio_clip_root` | `null` = not chosen | Studio only: the folder OBS plays from. Asked on the setup page right after enrollment (default `C:\BajaClips`); the worker creates it and grants the local Users group read access at every start (`worker/studio/clip_folder.py`). |
| `shared_tailnet` | `false` | Set by "Usar a rede atual" in `--first-run` (LIVESTACK-41 item 4): the PC stays on its own tailnet, into which the VPS is shared. Sent in every heartbeat; the orchestrator then never makes it a second holder or a peer source and tells it to deliver through the outbox. Cleared by "Registrar de novo" and by joining the Baja tailnet. |

After enrollment the Tailscale auth key is written to
`tailscale-authkey.txt` next to the config file. The installer runs
`tailscale up --authkey=…` with it and deletes the file; the daemon never
runs Tailscale itself.

## Folder layout

```
<config folder>/
├── worker.json
└── tailscale-authkey.txt        only between enrollment and the installer's tailscale up

<working folder>/                (defaults to the config folder)
├── raws/<sender>-<YYYYMMDD-HHMMSS>-<job id first 8>.<ext>
│                                held raws (readable names, LIVESTACK-41 item 12; older ones
│                                keep <job_id>.<ext>): served on :8710, never evicted, deleted
│                                only when the orchestrator lists the job in deletable_job_ids
├── raws/.raw-names.json         readable raw name → full job id
├── raws/.<name>.part            a download in progress (resumed after a crash or restart)
├── clips/<job_id>.mp4           the clip being rendered/delivered right now
├── clips/<job_id>.json          the inputs it was rendered from (cuts, fields): lets a
│                                restarted worker reuse it instead of rendering again
├── clips/<clip name>.<job_id>.<sha256>.mp4
│                                every clip this worker delivered, KEPT after delivery
│                                (clip name e.g. enduro-042-p3; older ones lack it)
│                                (served on :8710 GET /clip/{job_id}); see "Clips" below
├── clips/<prova>/<car>/<prova>-<car>-p<passagem>[-vN].mp4
│                                master only: the clip archive (every clip, every version)
├── tmp/                         proxy, thumbnail, overlay PNG, render in progress;
│                                emptied at every start
├── pending_acks.json            studio only: delivered ACKs not yet accepted by the
│                                orchestrator; replayed at start (see studio-acks below)
└── logs/worker.log              rotating, 10 MB × 5; every line has job=<id> or job=-

<studio clip root>/              (studio only; design §7, docs/api.md §8.1)
├── latest.m3u                   newest first, max 50, relative paths
├── .playlist-index.json         what latest.m3u is built from (hidden)
├── .clip-hashes.json            cached SHA-256 per clip (size + mtime), so a second
│                                delivery of the same clip is recognised without rereading it
└── <prova>/<car>/<prova>-<car 3 digits>-p<passagem>[-vN].mp4
                                 e.g. enduro/42/enduro-042-p3.mp4; -v2 only when the
                                 previous version was locked (on air) when a new one came
```

### Clips are kept after delivery (where they go)

Processed clips are the event's archive (design §11, as amended in
LIVESTACK-38). After a verified upload to the outbox or push to the studio
the worker does **not** delete its clip: it moves it to
`clips/<job_id>.<sha256>.mp4`, lists it in every heartbeat (`held_clips`)
and serves it on `:8710` so the master can copy it. Where a finished clip
is, at any moment:

* on the **studio box** (`<prova>/<car>/enduro-042-p3.mp4`), once delivered;
* in the **VPS outbox** while the studio is offline, and after its ACK
  until the master archived it (at most 72 h);
* on **the worker that rendered it**, until the master archived it *and*
  the worker runs short of space;
* on the **master**, in `clips/<prova>/<car>/enduro-042-p3.mp4` (and
  `-v2.mp4`, … for reprocessed passes), for good.

Kept clips count against the storage cap. Near the cap (under 4 GB free)
the worker deletes kept clips **the orchestrator says the master has
archived** (`archived_clip_sha256s` in the heartbeat answer), oldest
first, until there is room again; an unarchived clip is never deleted.
The master never evicts its archive.

Everything under `raws/`, `clips/` and `tmp/` counts against the cap,
partial files included. A partial raw is kept whenever a download stops
early (shutdown, crash, **lost lease**): if the same job is offered again,
even with other sources (a peer instead of the inbox), the download resumes
from it. Partial raws nobody resumed for 24 h are deleted at start.

## How it works (one paragraph per thread)

* **heartbeat** (`heartbeat.py`): every 15 s sends free bytes, cap,
  tailnet address, ports, the leases in use (which renews them), held job
  ids, new measurements, the role it runs as and the render features
  this version can do (`render_features.py`: `framing`, `no_audio`,
  LIVESTACK-50, installer 0.4.0; `pass_result`, LIVESTACK-47, installer
  0.5.0; the orchestrator gives renders that need one only to workers
  that declared it). An orchestrator older than contract 2.6.0
  refuses that field with 400: the beat is resent without it, and it is
  left out for 5 minutes. Acts on `lost` leases, `accepting_new_raws`,
  `deletable_job_ids` and `role`.
* **role switch** (`role_change.py`, LIVESTACK-42): when an admin presses
  "Tornar master" on Admin → Workers, the next heartbeat answer's `role`
  differs from the running one. The worker writes it into `worker.json`
  (nothing else changes) and stops cleanly; the Windows service is started
  again by NSSM 10 s later, now in the new role. Run by hand, start it
  again yourself. Only master ↔ worker; a studio never switches.
* **dispatcher** (`dispatcher.py`): asks `/next` only for kinds whose stage
  is idle, checks a raw fits before claiming, claims, and hands the task
  to its stage. `already_claimed`/`not_claimable` → ask again.
* **stage-…** (`stages.py`, `tasks/`): one thread per task kind, so a
  download, a proxy and a render run side by side, one of each. How a task
  ends is decided in `failures.py` (see "Failures" below).
* **make_proxy** probes the rendered proxy and sends its frame rate as the
  `Frame-Rate` header on every proxy chunk (e.g. `29.97`); left out when
  ffprobe cannot tell.
* **archive_clip** (master only, lowest priority): copies one finished
  clip version into the archive tree: its own kept copy is moved; others
  are downloaded from the worker that keeps it (`:8710/clip/…`, no token)
  or the VPS outbox (with the token), hash-checked, then confirmed with
  `POST /jobs/{id}/clip-archived`.
* **process** renders every clip at 1920×1080 (LIVESTACK-50): the raw's
  shape as ffprobe sees it after its rotation note, plus the task's
  `rotation` ("Girar"), decide the plan (`shared.framing`): a 16:9 picture
  gets the old `scale=-2:1080`, any other is fitted and filled per
  `framing` (blurred, black bars, or a cropped band at `crop_position`).
  The overlay is always drawn 1920 wide. `no_audio` ("Sem áudio") renders
  without sound. When the task carries `pass_result_text` (LIVESTACK-47,
  e.g. "66 s · 4 cones → 86 s", built by the orchestrator; the worker
  never fetches results), the overlay gets a third pill *under* the role
  pill (`overlay_result.py`): sized like the theme's scoreboard rows
  (`--board-row-height`, `--size-board`), in capitals, shrunk then cut with
  "…" like the other pills. The name and role pills do not move. Bebas
  Neue has no "→", so the arrow is drawn as a shape (`overlay_arrow.py`).
  The text is in the clip's sidecar (a corrected result renders again)
  and in the render-inputs report. Then it tells the orchestrator what the clip was rendered
  from (LIVESTACK-43, `PUT /jobs/{id}/render-inputs` under the lease:
  cuts, review fields, the render options, the overlay texts as drawn,
  the SHA-256 of the vendored `overlay_theme/tokens.css` and of the
  clip), so the review UI
  can warn when a reviewer edits a job whose clip is already on air. A
  refused report is logged and the clip is delivered anyway. A reprocess
  arrives with a `…-vN.mp4` `clip_relative_path`, so the studio puts the
  new version beside the one on air instead of over it.
  Then it delivers the clip the way the offer's `delivery` says (the
  orchestrator decides; the worker never looks at `GET /workers`):
  `outbox` → chunked upload to the outbox; `direct` → check the studio
  answers `GET /health` within 3 s (else the outbox at once, and the studio
  is remembered as unreachable for 5 minutes), push to
  `delivery.studio_url`, then wait up to 60 s for the studio's ACK to end
  the lease. If the push failed, or no ACK came and the job is not already
  delivered with this clip, upload to the outbox.
* **upload_original** (LIVESTACK-41 item 15): a reviewer asked for the
  original; upload the held raw, chunked, to the task's `upload_url`.
  A worker meeting an orchestrator that does not know this kind (400 on
  `/next`) stops asking for it.
* **peer-file-server / studio-receiver** (`servers.py`, `studio/`):
  uvicorn in a background thread.
* **studio clip placement** (`studio/placement.py`, item 13): a verified
  clip is moved into the folder without ever failing on Windows locks.
  Same content already there (any version name) = delivered, nothing
  renamed; a lock is retried with a short backoff; a clip on air that
  stays locked is never overwritten, the new version goes beside it as
  `…-v2.mp4`, which is what latest.m3u then plays and what is ACKed. The
  older version is deleted once nothing holds it. The `deliver` task skips
  the download entirely when the folder already holds that clip.
* **studio-acks** (studio): after a pushed clip is stored, adds it to
  `latest.m3u` and sends `POST /jobs/{id}/delivered`, retrying every 10 s.
  Owed ACKs are written to `pending_acks.json` (temp file + rename) and
  replayed after a restart. An ACK leaves the list when accepted, or when
  the orchestrator answers a 4xx that says it no longer applies (404 job
  gone, 409 reprocessed/discarded meanwhile, 422 mismatch); 401/403/408/429
  and network errors keep it for the next round.

### Status page, pause and the tray icon (LIVESTACK-38 item 9)

* **The page never reloads itself** (item 14). `page_assets/page.js`
  fetches `GET /api/page` every 5 s and replaces only the live region (no
  form field ever lives there); the form region changes only when the
  server wants another form and nobody is using the current one (focus or
  typed text). An opened "Detalhes" stays open.
* **Status page** (`status_page.py`, pt-BR): once enrolled,
  `http://localhost:8700` shows one big state line in the tray icon's
  colour, what the worker is doing (video, prova, car), space used vs.
  cap, held raws (and clips held/archived once item 7 reports them),
  last contact with the server, the Pausar/Retomar and Desligar buttons,
  and the technical details (worker id, URLs, version, last error, the
  free-form notes) folded under "Detalhes".
* **Local JSON API** (`local_api.py`), localhost only, no login:

  | Method & path | Does | Answer |
  | --- | --- | --- |
  | `GET /api/status` | — | the status document: `phase`, `enrolled`, `enrollment_refused`, `connection_step_pending`, `config_problem` (file, line, column, line text, hint), `tailnet_problem` (kind, saved and current address), `identity_rejected`, `can_re_register`, `paused`, `last_contact_at`, `unreachable_for_s`, `tasks[]` (`kind`, `job_id`, `prova`, `car_number`, `percent`), `storage` (`used_bytes`, `cap_bytes`, `near_cap`), `held_raws`, `clips_held`, `clips_archived`, `studio` (clip folder and `latest.m3u` path), `details` (never the bearer token) |
  | `GET /api/page` | — | the page's live parts as HTML (`live_html`, `form_key`, `form_html`, `title`); `page_assets/page.js` swaps them in place |
  | `POST /api/re-register` | "Registrar de novo" | the status document |
  | `POST /api/pause` | "Pausar" | the new status document |
  | `POST /api/resume` | "Retomar" | the new status document |
  | `POST /api/stop` | "Desligar": `sc stop BajaWorker` on Windows (exiting would make NSSM restart us), else stops the process | the status document |

  POSTs must have `Content-Type: application/json` and no foreign
  `Origin`; together with the localhost `Host` check this keeps other web
  sites in the volunteer's browser from pressing the buttons (a plain
  HTML form cannot send JSON, and a cross-site `fetch` with JSON needs a
  CORS preflight we never allow). Anything else → 403.
* **Pause** (`pause.py`): the dispatcher asks `/next` for nothing while
  paused; running tasks finish normally; heartbeats go on with
  `paused: true` (docs/api.md §5); the peer file server keeps serving held
  raws. No administrator rights needed.
* **Tray icon** (`tray/`): `state.py` turns the status document and the
  Windows service state into colour (green working, blue idle, yellow
  paused/waiting, red problem, grey off), a one-line pt-BR tooltip, the
  menu (Abrir painel, Pausar/Retomar, Desligar/Ligar o worker, Concluir
  conexão while the join is pending or the tailnet is missing, Registrar
  de novo behind a Yes/No box, Sair do ícone) and the notifications, each
  raised once (connection step pending, not on the Baja tailnet, broken
  worker.json, identity rejected, disk near the cap, enrollment refused,
  service stopped unexpectedly; never per job). It has
  no GUI imports and is unit-tested on Linux. `service.py` wraps
  `sc query/start/stop BajaWorker` (the installer lets the Users group
  start and stop that one service; on "access denied" it falls back to a
  UAC prompt). `app.py` is the thin pystray layer, polling every 3 s.
  `pystray` is a Windows-only dependency.

### Where the bearer token goes

Only to two origins (scheme, host, port): `orchestrator_url` and
`tailnet_base_url`. Every request asks `OrchestratorClient.auth_headers_for(url)`,
which applies the rule in `token_origins.py`. Peers (`:8710`), the studio
(`:8720`) and any other URL an offer might carry never get it.

### Failures (`failures.py`, design §17)

| Ending | When | What the worker does |
| --- | --- | --- |
| `failed`, scope `job` | the input is bad: every source sent a raw (or outbox clip) whose SHA-256 differs from the offer's; ffprobe cannot read the raw; the offer is unusable (unsafe clip path) | `POST /jobs/{id}/failed` — the job fails at once |
| `failed`, scope `worker` | this machine could not do it: disk full / other `OSError`, local ffmpeg crash or non-zero exit while rendering, network gave up after the retries, raw missing from disk, a bug in the worker | `POST /jobs/{id}/failed` — the task goes to another worker |
| lease left to expire | the orchestrator itself is unreachable (a report would not arrive either); an unexpected 4xx from it; network trouble in the master's `download_raw` or the studio's `deliver` (only they can do those tasks, and a `worker` report would exclude them from that job) | stop renewing the lease; the task is offered again later |
| cancelled | lease lost, or shutdown | stop; keep resumable files |

## Tests

`uv run pytest tests/worker` (no network, no GPU). `test_real_ffmpeg.py`
runs the real ffmpeg on a generated clip and is skipped when ffmpeg is
not installed (as in CI).

Running daemons inside a test process: build `Daemon(config, config_path,
StatusBoard(), threading.Event(), media=…, transport=…, timings=…)`, call
`start()`, later `stop()`. It installs no signal handlers. `DaemonTimings`
shortens the heartbeat interval, the pause between `/next` rounds, the
60 s wait after a direct push, report/transfer retry delays and the ACK
retry delay; ports set to `0` bind any free port
(`daemon.listening_port`), or `start_servers=False` plus `daemon.http_app()`
mounts the role's app on an in-memory transport instead.
