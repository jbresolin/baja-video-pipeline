# Orchestrator

The VPS service of the Baja video pipeline (design §2, LIVESTACK-24/-28):
WhatsApp intake, the jobs DB, task placement for workers, the review API
and the delivery manifest. It implements [`openapi.yaml`](openapi.yaml);
the behaviour is described in [`docs/api.md`](../docs/api.md).

## Run it locally (no network, no WhatsApp, no Tailscale)

```sh
uv sync
uv run python -m orchestrator.testing.dev_server
```

This starts the real service on <http://127.0.0.1:8000> with in-process
fakes for Evolution (bot replies are printed), Tailscale (`/enroll` mints
fake keys) and the Baja app (car `n` belongs to "Equipe n"). Its data goes
to `.scratch/bvp-dev/`. Credentials: admin token `dev-admin`, webhook
secret `dev-secret`, reviewer `ana` / `ana`. Any `BVP_*` variable you set
overrides these defaults.

Send it a "WhatsApp" video from another terminal. The fake poster encrypts
the file like WhatsApp, serves it on a fake CDN (`:8790`, which the dev
server already trusts) and posts the webhook:

```sh
uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4   # needs ffmpeg
uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4
uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4 --as-video  # low_res
```

Any file works as the "video"; the orchestrator never decodes raws.
Then look around:

```sh
curl -u ana:ana 'http://127.0.0.1:8000/review/queue?state=received'
curl -u ana:ana http://127.0.0.1:8000/fleet
curl -u ana:ana 'http://127.0.0.1:8000/events?limit=20'      # the event log, oldest first

# enroll a worker by hand
curl -s -H 'Authorization: Bearer dev-admin' -H 'Content-Type: application/json' \
     -d '{"volunteer_name": "me", "role": "master"}' \
     http://127.0.0.1:8000/admin/enrollment-tokens
curl -s -H 'Content-Type: application/json' http://127.0.0.1:8000/enroll -d '{
  "enrollment_token": "<token from above>", "worker_name": "my-pc",
  "capabilities": {"has_nvenc": false, "video_encoder": "libx264"},
  "storage_cap_bytes": 100000000000}'
```

`POST /intake/test-upload` (dev mode only) is the shortcut without the CDN:

```sh
curl -H 'Authorization: Bearer dev-admin' -F file=@.scratch/clip.mp4 \
     http://127.0.0.1:8000/intake/test-upload
```

## Tests

```sh
uv run pytest tests/orchestrator          # ~12 s, no network
uv run pytest tests/orchestrator/test_end_to_end.py -v
```

`orchestrator.testing.harness.build_harness(tmp_path)` gives a whole
orchestrator wired to the fakes, a `ManualClock` and an in-memory HTTP
client; `orchestrator.testing.scenarios` has the building blocks (send a
video, claim, upload in chunks, fetch like Caddy). Background tasks never
run on their own in tests: call `orchestrator.background.sweepers.*_once`.

## Docker

Build from the repo root (the image needs `shared/` too):

```sh
docker build -f orchestrator/Dockerfile -t baja-orchestrator .
```

python:3.12-slim + uv, runs as uid/gid 10001, listens on 8000, has a
`/health` healthcheck. It refuses to start, naming the missing variables,
until every required setting is present (docs/api.md §10). It writes
`inbox/ proxies/ thumbs/ outbox/ db/` under `BVP_DATA_DIR`, so on the VPS:

```sh
sudo install -d -o 10001 -g 10001 /srv/pipeline/{inbox,proxies,thumbs,outbox,db}
```

## Code map

| Path | What |
| --- | --- |
| `config.py` | `BVP_*` / `TAILSCALE_*` settings; fail fast with names |
| `app.py`, `__main__.py` | wiring (`build_context`, `create_app`) and `python -m orchestrator` |
| `db/` | SQLite in WAL mode, numbered `migrations/*.sql` applied at startup |
| `repositories/` | plain SQL, one module per table group |
| `domain/` | the rules: intake, enrollment, `task_rules` (who gets what, delivery plan), `worker_loop` (`/next` order incl. the master's backfill), `task_results` (failure scopes), leases, uploads, review, delivery, fleet, `event_log`, `/auth/file` |
| `clients/` | Evolution `sendText`, Tailscale key minting, team lookup (`teams.py`: `BVP_TEAMS_FILE` CSV first, then the Baja app, cached) |
| `api/` | FastAPI routes, the four auth schemes, the error shape |
| `background/` | lease expiry, inbox cleanup, proxy eviction, outbox cleanup |
| `testing/` | fakes, harness, scenarios, the dev server |

Every state change goes through `domain/transitions.move_job`, which asks
`shared.state_machine` and appends to the `events` table (who, when, why).
`GET /jobs/{id}/events` and `GET /events` expose that table to the review UI.

## Rules worth knowing (design §17)

* **Failures.** `POST /jobs/{id}/failed` with `scope: "job"` fails the job
  at once. `scope: "worker"` (the default) excludes that worker from the
  task on that job and re-offers it; the job fails on the second different
  worker for the same task (`WORKER_SCOPE_FAILURES_BEFORE_JOB_FAILS`).
* **Master backfill.** After all live work, `/next` offers the master
  `download_raw` for every done or failed job it does not hold.
* **Delivery plan.** Process offers say `direct` (with the studio's receive
  URL) when a studio heartbeated within 45 s, else `outbox`.
* **Teams.** `BVP_TEAMS_FILE` (CSV `car_number,team_name,school`, reloaded
  on change) first, then `BVP_BAJA_APP_URL`; both optional. Unknown cars
  answer 404 and leave `team_name` empty.
