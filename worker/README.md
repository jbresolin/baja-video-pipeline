# Worker app (`python -m worker`)

One program, three roles (design §13). The role comes from the enrollment
token, not from the config:

| Role | Does | Listens on |
| --- | --- | --- |
| `master` | holds **every** raw (design §4), makes proxies, renders clips, backfills raws it missed from peers | 8710 peer file server |
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
| `orchestrator_url` | `http://127.0.0.1:8000` | Caddy's tailnet listener, e.g. `http://100.101.102.103:8080`. Every call except `/enroll`. |
| `enroll_url` | `null` = `orchestrator_url` | Public 443 site that routes `POST /enroll` (reachable before Tailscale is up). |
| `enrollment_token` | `null` | One-time token. Removed after a successful enrollment. |
| `worker_id`, `bearer_token`, `role` | `null` | Saved by enrollment. All three set = enrolled. Keep the file private: the token is the worker's identity. |
| `worker_name` | host name | Shown in the fleet view. |
| `working_folder` | the config file's folder | Where `raws/`, `clips/`, `tmp/`, `logs/` live. |
| `storage_cap_bytes` | 100 GiB | Upper limit for everything in the working folder. Volunteers stop taking new raws near it; held raws are never evicted. The master ignores it for raws (only real disk space stops it). |
| `ffmpeg_path` | `ffmpeg` | The bundled BtbN build on Windows. |
| `ffprobe_path` | `null` = next to `ffmpeg_path` | |
| `tailnet_address` | `null` = `tailscale ip -4` | Sent in heartbeats so peers and the studio can be reached. |
| `listen_address` | `null` = `tailnet_address`, else all interfaces | Address of the 8710/8720 servers. Binding to the Tailscale IP keeps raws off the local LAN. |
| `file_server_port` | 8710 | Peer file server (master, worker). |
| `receiver_port` | 8720 | Clip receiver (studio). |
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
└── logs/worker.log              rotating, 10 MB × 5; every line has job=<id> or job=-

<studio clip root>/              (studio only; design §7, docs/api.md §8.1)
├── latest.m3u                   newest first, max 50, relative paths
├── .playlist-index.json         what latest.m3u is built from (hidden)
└── <prova>/<car>/<passagem>.mp4
```

Everything under `raws/`, `clips/` and `tmp/` counts against the cap,
partial files included. Partial raws nobody resumed for 24 h are deleted at
start.

## How it works (one paragraph per thread)

* **heartbeat** (`heartbeat.py`): every 15 s sends free bytes, cap,
  tailnet address, ports, the leases in use (which renews them), held job
  ids and new measurements. Acts on `lost` leases, `accepting_new_raws` and
  `deletable_job_ids`.
* **dispatcher** (`dispatcher.py`): asks `/next` only for kinds whose stage
  is idle, checks a raw fits before claiming, claims, and hands the task
  to its stage. `already_claimed`/`not_claimable` → ask again.
* **stage-…** (`stages.py`, `tasks/`): one thread per task kind, so a
  download, a proxy and a render run side by side, one of each. Outcomes:
  success; `TaskFailedError` → `POST /jobs/{id}/failed` with ffmpeg's last
  lines; `TaskAbandonedError` (network, nothing reachable) → stop renewing
  the lease and let it expire; `TaskCancelledError` → lease lost or
  shutdown.
* **peer-file-server / studio-receiver** (`servers.py`, `studio/`):
  uvicorn in a background thread.
* **studio-acks** (studio): after a pushed clip is stored, adds it to
  `latest.m3u` and sends `POST /jobs/{id}/delivered`, retrying until accepted.
* **backfill** (master): every 10 minutes compares `/manifest` with the
  raws on disk and copies missing ones from a confirmed holder's `:8710`.

## Tests

`uv run pytest tests/worker` (no network, no GPU). `test_real_ffmpeg.py`
runs the real ffmpeg on a generated clip and is skipped when ffmpeg is
not installed (as in CI).
