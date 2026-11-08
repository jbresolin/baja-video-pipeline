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
fake keys) and the Baja results API (the docs' event `26BR`: set it in
Admin → Configurações). Its data goes to `.scratch/bvp-dev/`. Credentials:
admin token `dev-admin`, webhook secret `dev-secret`, users `ana` / `ana`
(admin) and `bruno` / `bruno`. Any `BVP_*` variable you set
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

### Dev mode (`BVP_DEV_MODE=1`)

The dev server turns it on. It does two things that must never happen on
the VPS, so the orchestrator **refuses to start** with `BVP_DEV_MODE=1`
unless `BVP_PUBLIC_BASE_URL` is `http://localhost…`, `http://127.0.0.1…`
or `http://[::1]…`:

* `POST /intake/test-upload` (below) creates jobs without WhatsApp.
* The orchestrator serves Caddy's static paths itself (`api/routes_dev_files.py`,
  design §18), so workers work with no Caddy in front:

  | Path | Who may read it (same as production) |
  | --- | --- |
  | `/inbox/<job>.<ext>` | the worker holding that job's `download_raw` lease (`/auth/file` rules) |
  | `/outbox/<job>.mp4` | the studio (`/auth/file` rules) |
  | `/proxies/<job>.mp4`, `/thumbs/<job>.jpg` | a reviewer (basic auth, like Caddy) |

  GET and HEAD, with `Range` (206) like Caddy's `file_server`; dot-files
  and `*.part` are never served. The routes are left out of `/openapi.json`.

**Whole pipeline on one PC** (orchestrator + fake webhook poster + master
+ worker + studio, ending with a clip in a local box folder): the
five-terminal recipe is in [`worker/README.md`](../worker/README.md#locally-the-whole-pipeline-on-one-pc-five-terminals).

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
| `domain/` | the rules: sessions, users/contacts/provas/teams admin, the welcome message to new contacts (`welcome.py`), `results_sync`, `first_start` imports, intake, enrollment, `recording_time` (when a video was filmed), `car_videos` (one car's videos in a prova), `whatsapp_notes` / `intake_notes` / `sender_messages` (the sender's other messages: read, kept, edited or deleted, and which video they belong to), `task_rules` (who gets what, delivery plan), `render_features` (which worker versions may render which jobs, LIVESTACK-50), the official pass results (LIVESTACK-47: `pass_results_config` checks the Settings' JSON, `results_table` reads the API's table, `pass_text` builds the overlay's result line, `pass_results` gives a job its line, `pass_results_fetch` fetches every 30 s, `pass_results_admin` serves Settings and "Testar"; LIVESTACK-48: `pass_clock` gives a job its running clock), `vertical_reply` (the bot's "grave na horizontal"), LIVESTACK-51: `presence` (who has a job page open, in memory), `duplicates` (same video sent again: discarded at once), `phones` (one form per phone number) and `waiting_prova` (apply a default prova to waiting videos), `worker_loop` (`/next` order incl. the master's backfill), `task_results` (failure scopes), leases, uploads, review, delivery, fleet, `event_log`, `/auth/file` |
| `clients/` | Evolution `sendText` and instance status, Tailscale key minting, the Baja results API (`results_api.py`), the old teams CSV parser |
| `api/` | FastAPI routes, the auth schemes and roles, rate limits, the error shape |
| `background/` | lease expiry, inbox cleanup, proxy eviction, outbox cleanup, sender-messages retention |
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
* **Provas and teams** are synced from the Baja results API for the event
  set on the admin Settings page (`domain/results_sync.py`); manual provas
  and team corrections live alongside. Unknown cars answer 404 and leave
  `team_name` empty.
* **Pass results** (LIVESTACK-47, design §32): configured provas' results
  tables are fetched every 30 s while they have jobs (delivered ones
  included) and the event is not finished, then once more and no more; a
  failure keeps the last good answer. The result
  line goes to the worker as `ProcessTask.pass_result_text`, only to
  workers that declare `pass_result` (installer 0.5.0).
* **Running clock** (LIVESTACK-48, design §33): from the reviewer's
  largada and the run's official time (same tables), as
  `ProcessTask.pass_clock`, only to workers that declare `clock` and,
  since LIVESTACK-53 (design §36), `clock_top_right` (installer 0.7.0).
  The largada, the official time, the toggle and the unit (the
  configuration's `clock.unit`, default "s"; not compared for clips
  recorded without one) are render inputs.
  `GET /jobs/{id}/pass-preview` (`domain/pass_preview.py`) answers the
  result line and the clock for prova, car and passagem not saved yet.
* **Users** sign in with `POST /auth/login` (`domain/sessions.py`); review
  routes take any session, `/admin/*` an admin session or the admin token.
