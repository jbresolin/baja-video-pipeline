# Worker app (`python -m worker`)

One program, three roles (design §13). The role comes from the enrollment
token, not from the config:

| Role | Does | Listens on |
| --- | --- | --- |
| `master` | holds **every** raw (design §4), makes proxies, renders clips. Raws it missed while offline are simply offered to it again (from a peer once the VPS copy is gone) | 8710 peer file server |
| `worker` | holds raws it is offered while under its storage cap, makes proxies, renders clips | 8710 peer file server |
| `studio` | never holds raws; receives pushed clips, pulls outbox clips (`deliver`), keeps `latest.m3u` | 8720 clip receiver |

All roles also serve the setup/status page on `http://127.0.0.1:8700`
(localhost only).

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
* On first run (no `worker_id` in the config) it serves only the setup page
  until a token is entered, then calls `POST /enroll` and continues.

### Locally against the orchestrator's dev mode

1. Start the orchestrator (LIVESTACK-24) on `http://127.0.0.1:8000` with
   `BVP_DEV_MODE=1` and `BVP_TAILNET_BASE_URL=http://127.0.0.1:8000`, so
   task offers point at it directly (there is no Caddy locally).
2. Mint one enrollment token per role you want to run:
   ```
   curl -s -X POST http://127.0.0.1:8000/admin/enrollment-tokens \
     -H "Authorization: Bearer $BVP_ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"volunteer_name": "local master", "role": "master"}'
   ```
3. Give each worker its own folder and config. Several workers on one PC
   need different ports. Example `.scratch/master/worker.json`:
   ```json
   {
     "orchestrator_url": "http://127.0.0.1:8000",
     "enrollment_token": "<token from step 2>",
     "tailnet_address": "127.0.0.1",
     "storage_cap_bytes": 20000000000
   }
   ```
   A second worker: `"file_server_port": 8711, "setup_page_port": 8701`.
   A studio: `"receiver_port": 8720, "setup_page_port": 8702` and
   optionally `"studio_clip_root": "/path/OBS/plays/from"`.
4. Start each one: `uv run python -m worker --config .scratch/master/worker.json --console`.
5. Feed it a video:
   ```
   uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4
   curl -s -X POST http://127.0.0.1:8000/intake/test-upload \
     -H "Authorization: Bearer $BVP_ADMIN_TOKEN" \
     -F file=@.scratch/clip.mp4 -F sender_name=test -F received_as=test_upload
   ```
   The master downloads it and renders the proxy. Review it in the review
   UI, mark it ready, and a holder renders the clip and pushes it to the
   studio (or the outbox when no studio is running).

Without the orchestrator, the test suite runs every role against an
in-memory fake (`tests/worker/fake_orchestrator.py`).

## Config file reference (`worker.json`)

Written by the installer and the setup page. Unknown keys are refused.

| Key | Default | Meaning |
| --- | --- | --- |
| `orchestrator_url` | `http://127.0.0.1:8000` | Caddy's tailnet listener, e.g. `http://100.101.102.103:8080`. Every call except `/enroll`. One of the two origins that get the bearer token. |
| `enroll_url` | `null` = `orchestrator_url` | Public 443 site that routes `POST /enroll` (reachable before Tailscale is up). |
| `enrollment_token` | `null` | One-time token. Removed after a successful enrollment. |
| `worker_id`, `bearer_token`, `role` | `null` | Saved by enrollment. All three set = enrolled. Keep the file private: the token is the worker's identity. |
| `tailnet_base_url` | `null` | Saved by enrollment from the `/enroll` answer: Caddy's tailnet listener that serves `/inbox/*` and `/outbox/*`. The second (and last) origin that gets the bearer token. `null` in configs enrolled before it existed: then only `orchestrator_url` gets the token. |
| `worker_name` | host name | Shown in the fleet view. |
| `working_folder` | the config file's folder | Where `raws/`, `clips/`, `tmp/`, `logs/` live. |
| `storage_cap_bytes` | 100 GiB | Upper limit for everything in the working folder. Volunteers stop taking new raws near it; held raws are never evicted. The master ignores it for raws (only real disk space stops it). |
| `ffmpeg_path` | `ffmpeg` | The bundled BtbN build on Windows. |
| `ffprobe_path` | `null` = next to `ffmpeg_path` | |
| `tailnet_address` | `null` = `tailscale ip -4` | Sent in heartbeats so peers and the studio can be reached. |
| `listen_address` | `null` = `tailnet_address`, else all interfaces | Address of the 8710/8720 servers. Binding to the Tailscale IP keeps raws off the local LAN. |
| `file_server_port` | 8710 | Peer file server (master, worker). `0` = any free port (tests); heartbeats report the port really bound. |
| `receiver_port` | 8720 | Clip receiver (studio). `0` as above. |
| `setup_page_port` | 8700 | Setup/status page on 127.0.0.1. |
| `studio_clip_root` | `null` = `<working folder>/box` | Studio only: the folder OBS plays from. |

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
├── raws/<job_id>.<ext>          held raws: served on :8710, never evicted, deleted only when
│                                the orchestrator lists the job in deletable_job_ids
├── raws/.<job_id>.<ext>.part    a download in progress (resumed after a crash or restart)
├── clips/<job_id>.mp4           a rendered clip until it is delivered
├── clips/<job_id>.json          the inputs it was rendered from (cuts, fields): lets a
│                                restarted worker reuse it instead of rendering again
├── tmp/                         proxy, thumbnail, overlay PNG, render in progress;
│                                emptied at every start
├── pending_acks.json            studio only: delivered ACKs not yet accepted by the
│                                orchestrator; replayed at start (see studio-acks below)
└── logs/worker.log              rotating, 10 MB × 5; every line has job=<id> or job=-

<studio clip root>/              (studio only; design §7, docs/api.md §8.1)
├── latest.m3u                   newest first, max 50, relative paths
├── .playlist-index.json         what latest.m3u is built from (hidden)
└── <prova>/<car>/<passagem>.mp4
```

Everything under `raws/`, `clips/` and `tmp/` counts against the cap,
partial files included. A partial raw is kept whenever a download stops
early (shutdown, crash, **lost lease**): if the same job is offered again,
even with other sources (a peer instead of the inbox), the download resumes
from it. Partial raws nobody resumed for 24 h are deleted at start.

## How it works (one paragraph per thread)

* **heartbeat** (`heartbeat.py`): every 15 s sends free bytes, cap,
  tailnet address, ports, the leases in use (which renews them), held job
  ids and new measurements. Acts on `lost` leases, `accepting_new_raws` and
  `deletable_job_ids`.
* **dispatcher** (`dispatcher.py`): asks `/next` only for kinds whose stage
  is idle, checks a raw fits before claiming, claims, and hands the task
  to its stage. `already_claimed`/`not_claimable` → ask again.
* **stage-…** (`stages.py`, `tasks/`): one thread per task kind, so a
  download, a proxy and a render run side by side, one of each. How a task
  ends is decided in `failures.py` (see "Failures" below).
* **make_proxy** probes the rendered proxy and sends its frame rate as the
  `Frame-Rate` header on every proxy chunk (e.g. `29.97`); left out when
  ffprobe cannot tell.
* **process** delivers the clip the way the offer's `delivery` says (the
  orchestrator decides; the worker never looks at `GET /workers`):
  `outbox` → chunked upload to the outbox; `direct` → push to
  `delivery.studio_url`, then wait up to 60 s for the studio's ACK to end
  the lease, and upload to the outbox if the push failed or no ACK came.
* **peer-file-server / studio-receiver** (`servers.py`, `studio/`):
  uvicorn in a background thread.
* **studio-acks** (studio): after a pushed clip is stored, adds it to
  `latest.m3u` and sends `POST /jobs/{id}/delivered`, retrying every 10 s.
  Owed ACKs are written to `pending_acks.json` (temp file + rename) and
  replayed after a restart. An ACK leaves the list when accepted, or when
  the orchestrator answers a 4xx that says it no longer applies (404 job
  gone, 409 reprocessed/discarded meanwhile, 422 mismatch); 401/403/408/429
  and network errors keep it for the next round.

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
