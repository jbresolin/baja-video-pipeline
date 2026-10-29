# Review UI (LIVESTACK-27, fleet view LIVESTACK-26)

The web app reviewers use at `https://<PIPELINE_DOMAIN>/review/`. It runs as
its own service (design §16) and keeps **no data of its own**. Every page is
built from the orchestrator's HTTP API, called with the reviewer's own
`Authorization: Basic …` header, which Caddy has already checked. The
orchestrator checks it again and records the reviewer in `Job.reviewed_by`.

```
browser ──443──▶ Caddy ──/review*──▶ review-ui:8001 ──HTTP API──▶ orchestrator:8000
                   └──/proxies/*, /thumbs/* served by Caddy itself (Range, no app in the byte path)
```

Caddy routes only `/review*` to this service; the orchestrator's API is not
public. So the page scripts call the UI's own JSON endpoints under
`/review/api/`, and the UI passes those calls on to the orchestrator.

## Pages

| Path | What |
| --- | --- |
| `/review/` | Queue: awaiting review + failed by default; filters by state, prova, low-res; auto-refresh every 5 s; `↑↓`/`j k` + `Enter` |
| `/review/jobs/{id}` | Proxy player, cut in/out marks, fields, actions, status strip, event log from `GET /jobs/{id}/events` (refreshes every 5 s; if the log cannot be read the page still works and says so) |
| `/review/clips` | Delivery manifest, newest first, prova filter, copyable studio path |
| `/review/fleet` | Workers (role, online, heartbeat age, held raws, storage vs cap, stats, leases, last 5 events from `GET /events?worker_id=…&limit=5`), VPS disk, jobs per state; refreshes every 10 s |
| `/healthz` | Container health check (not routed by Caddy) |

Job page keys: `Space`/`k` play, `i`/`o` set in/out, `Shift+i`/`o` jump to
them, `←`/`→` one frame, `Shift+←`/`→` one second, `-`/`=` nudge the last
mark by a frame, `p` play the cut, `Ctrl+S` save, `Ctrl+Enter`
ready/reprocess/retry, `?` shows the list. A frame is `1 / Job.frame_rate`
seconds (the proxy's fps, measured by the holder that uploaded it), or
1/30 s when the job has no frame rate yet; the server puts the value in
`data-frame-rate` on the page and the shortcut list shows it.

Every action sends the version of the job the page was loaded with
(`expected_version`). If someone else changed the job first, the
orchestrator answers 409. The page then reloads and says so, listing the
values the reviewer had typed.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `BVP_ORCHESTRATOR_URL` | `http://127.0.0.1:8000` | Orchestrator API (`http://orchestrator:8000` in compose) |
| `BVP_PUBLIC_BASE_URL` | empty | Public site; proxy/thumbnail paths are made absolute on it. Empty = same site as the UI |
| `BVP_REVIEW_UI_PORT` | `8001` | Listening port |
| `BVP_ORCHESTRATOR_TIMEOUT_S` | `10` | Per-call timeout; slower calls show a 502 page |
| `BVP_STUDIO_CLIP_ROOT` | empty | Optional clip folder on the studio box (e.g. `D:\Baja\clips`), so the clips page copies full paths |

## Run locally

```sh
uv run python tests/review_ui/dev_server.py        # fake orchestrator, http://127.0.0.1:8001/review/ (ana / secret)
uv run python -m review_ui                         # against a real orchestrator at BVP_ORCHESTRATOR_URL
uv run pytest tests/review_ui
```

`dev_server.py --media-dir .scratch/media` also serves
`proxies/<job_id>.mp4` and `thumbs/<job_id>.jpg` from that folder, the way
Caddy serves them on the VPS.

## Image

```sh
docker build -f review_ui/Dockerfile -t ghcr.io/jbresolin/baja-review-ui:<tag> .   # from the repo root
```

The image is `python:3.12-slim` with uv, runs as non-root user 10001, listens
on 8001, and has a health check on `/healthz`. In `infra/vps/docker-compose.yml`
it is the `review-ui` service (image `$REVIEW_UI_IMAGE`), reached by the
base Caddy as `bvp-review-ui:8001` on `baja-edge`.

## Code map

| File | What |
| --- | --- |
| `app.py` | `create_app()`: wiring, static files, routers |
| `orchestrator_client.py` | Every orchestrator call, with the reviewer's Authorization |
| `routes_queue.py`, `routes_job.py`, `routes_clips_fleet.py` | HTML pages and their refresh fragments |
| `routes_api.py` | JSON endpoints for the page scripts (review actions, proxy request, team lookup) |
| `errors.py` | Error page for browsers, JSON errors for scripts, 401 sign-in challenge |
| `job_view.py`, `queue_view.py`, `clips_and_fleet_view.py` | Plain functions that turn API data into what the pages show |
| `event_log_view.py` | Orchestrator events (`Event`) in words: actor names, state changes, reasons; used by the job and fleet pages |
| `templates/`, `static/` | Jinja2 templates; CSS and small ES modules (no build step) |
