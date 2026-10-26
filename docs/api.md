# Orchestrator API contract

Everything the orchestrator, the worker app (roles `master`, `worker`,
`studio`) and the review UI need to agree on. The machine-readable twin of
this page is [`orchestrator/openapi.yaml`](../orchestrator/openapi.yaml),
generated from `tools/openapi_contract/` over the pydantic models in
`shared.models`. **The models are the source of truth for field names and
types**; this page explains the behaviour around them. Design background:
[`design.md`](design.md).

## 1. Conventions

| Topic | Rule |
| --- | --- |
| Transport | HTTP/1.1 over the Baja tailnet. Orchestrator behind Caddy on the VPS. Locally everything also works on plain `http://127.0.0.1`. |
| Bodies | JSON (`application/json`, UTF-8) unless stated. Chunked uploads use `application/octet-stream`. |
| Unknown fields | Rejected with 400 `validation_error` (all our models use `extra="forbid"`). The Evolution webhook is the one exception. |
| Ids | Opaque strings (the orchestrator uses UUID4 hex). Never parse them. |
| Times | ISO 8601 with timezone, always UTC, e.g. `2026-10-25T14:03:07Z`. |
| Sizes | Integers, bytes. |
| Hashes | SHA-256 as 64 **lowercase** hex characters (`shared.hashing`). |
| Seconds | Float seconds from the start of the **raw** (`cut_in_s`, `duration_s`). The proxy is constant-frame-rate, so proxy time = raw time. |
| Path params | `{job_id}`, `{worker_id}`, `{car_number}`. |
| Constants | Heartbeat 15 s, lease TTL 45 s, peer file server port 8770, upload chunk 8 MiB: `shared.protocol`. |

## 2. Authentication

| Scheme | Header | Who | Issued by |
| --- | --- | --- | --- |
| `workerBearer` | `Authorization: Bearer <token>` | master, worker, studio | `POST /enroll`, one per worker |
| `adminBearer` | `Authorization: Bearer <token>` | João / scripts | orchestrator config (`BVP_ADMIN_TOKEN`) |
| `reviewerBasic` | `Authorization: Basic …` | review UI in the browser | orchestrator config (list of reviewer user/password pairs) |
| `webhookSecret` | `X-Webhook-Secret: <secret>` | Evolution | orchestrator config, set as a custom header in Evolution's webhook |

* The orchestrator checks all four itself (so everything is testable
  locally without Caddy or Tailscale). Bearer and Basic share the
  `Authorization` header and are told apart by scheme.
* Caddy serves proxies and thumbnails statically (no app process in the
  byte path, design §6) and protects `/proxies/*` and `/thumbs/*` with the
  same reviewer Basic credentials.
* Bearer tokens are worker identity. `/workers/{worker_id}/…` with someone
  else's id → 403. Studio-only endpoints called by another role → 403.
* Open endpoints: `GET /health`, and `POST /enroll` (the one-time token in
  the body is the credential).

| Endpoint group | Auth |
| --- | --- |
| `POST /intake/evolution-webhook` | webhookSecret |
| `POST /intake/test-upload`, `POST /admin/enrollment-tokens` | adminBearer |
| worker loop, `/workers`, `/outbox/*` | workerBearer |
| `/review/queue`, `PATCH …/review`, `…/regenerate-proxy`, `/teams/*` | reviewerBasic |
| `GET /jobs/{job_id}`, `GET /manifest` | reviewerBasic **or** workerBearer |
| `GET /fleet` | reviewerBasic **or** adminBearer |

## 3. Errors

Every non-2xx body has one shape (`shared.models.ErrorResponse`):

```json
{"error": {"code": "upload_offset_mismatch",
           "message": "expected a chunk starting at byte 8388608",
           "details": {"expected_offset": 8388608}}}
```

Branch on `error.code`, never on `message`.

| Status | `code` | When |
| --- | --- | --- |
| 400 | `validation_error` | Body/headers/query do not match the model |
| 401 | `unauthenticated` | Missing or wrong credentials |
| 401 | `enrollment_token_invalid` | Unknown, used or expired enrollment token |
| 403 | `forbidden` | Wrong worker id, or wrong role for the endpoint |
| 404 | `not_found` | No such job/worker/team, or the file is gone (raw already deleted from the VPS) |
| 409 | `illegal_transition` | The state machine forbids it (`details.current`, `details.requested`) |
| 409 | `already_claimed` | Someone else holds the lease for that task |
| 409 | `not_claimable` | Job not in a state for that task, or caller not eligible (e.g. not a holder) |
| 409 | `lease_lost` | Lease expired or revoked; stop the task |
| 409 | `version_conflict` | `expected_version` is stale (`details.current_version`) |
| 409 | `upload_offset_mismatch` | Chunk at the wrong offset (`details.expected_offset`) |
| 413 | `upload_too_large` | Upload over the declared length or the size limit |
| 413 | `inbox_full` | VPS inbox above its hard cap (~25 GB) |
| 416 | — | Range Not Satisfiable (plain HTTP semantics) |
| 422 | `hash_mismatch` | SHA-256 of the received/held file is wrong |
| 422 | `missing_review_fields` | `ready`/`reprocess` without all fields (`details.missing`) |
| 503 | `dev_mode_disabled` | `/intake/test-upload` without `BVP_DEV_MODE=1` |
| 503 | `upstream_error` | Tailscale API, Evolution or Baja app failed |

## 4. Job lifecycle

States and allowed moves live in `shared.state_machine`
(`validate_transition` raises `IllegalTransitionError` otherwise).

```
received ──proxy uploaded──▶ proxy_ready ──(automatic)──▶ awaiting_review
   ──review action=ready──▶ ready ──claim process──▶ claimed
   ──clip to outbox──▶ uploaded ──studio ACK──▶ delivered ──cleanup──▶ done
                       (direct push: studio ACK goes claimed → uploaded → delivered)

back to ready:  claimed (lease expired/revoked), and claimed | uploaded |
                delivered | done (review action=reprocess)
```

* **Holders** are tracked beside the state (`Job.holders`): the raw is
  downloaded by the master plus one more worker while the job moves on.
  The first holder to confirm gets the `make_proxy` task, so the job can
  reach `proxy_ready` before the second holder is done. When the
  second holder confirms, the VPS deletes its copy (`raw_on_vps=false`).
* **Proxy availability** (`proxy_available`) is also beside the state:
  eviction and `regenerate-proxy` never change the state.
* `waiting_reason` is a human sentence for the UI when a job is stuck
  (e.g. "waiting for a holder to come back online"), else `null`.
* `version` increases on every change of the job.

## 5. Worker loop

```
first run:   POST /enroll ──▶ worker_id, bearer token, Tailscale key ──▶ tailscale up
every 15 s:  POST /workers/{id}/heartbeat   (renews every lease it lists)
when free:   POST /workers/{id}/next        {accept_kinds: [...]}
             └─ task? ──▶ POST /jobs/{job_id}/claim {task_kind} ──▶ lease + task
                          do the work, then report (table below)
             └─ null  ──▶ wait retry_after_s, ask again
```

* `/next` returns an **offer**, not a reservation. Two workers may get the
  same offer; the first claim wins, the other gets 409 `already_claimed`
  and asks `/next` again.
* **At most one task per kind** per worker (design §5): leave a kind out of
  `accept_kinds` while you are busy with it.
* **Leases** last 45 s from the last renewal. Renew by listing the lease id
  in `current_lease_ids` on each heartbeat (or `POST …/lease/renew`). The
  heartbeat answer says `renewed` or `lost` per lease. On `lost`, stop the
  task and discard its partial output — the job was reassigned or the
  reviewer changed it. A lease also ends normally when you report the
  task's result.
* `accepting_new_raws: false` in the heartbeat answer means you are near
  your storage cap: you will get no new `download_raw`, but keep processing
  what you hold (held raws are never evicted).
* Report `measurements` (bytes + seconds per download/process/upload) in
  the next heartbeat; the orchestrator keeps the rolling `stats`.
* `held_job_ids` lists every raw on your disk so the orchestrator can
  reconcile holders after crashes.

| Task kind | Who gets it | What to do | How it ends |
| --- | --- | --- | --- |
| `download_raw` | master (always), one more worker; never studio | Fetch from `sources` in order with `shared.http_transfer.download_file` (Range resume + SHA-256), ffprobe it | `POST /jobs/{id}/holder-confirm` |
| `make_proxy` | a confirmed holder | Render thumbnail and proxy (`shared.ffmpeg`), `PUT …/thumbnail`, then chunked `POST …/proxy` | completing chunk of `…/proxy` |
| `process` | a confirmed holder | Render the clip (`build_final_render_command`, overlay PNG drawn by the worker). Push it to the studio (§7.2); if that fails, chunked `POST …/clip` to the outbox | studio ACK (direct) or completing chunk of `…/clip` (outbox) |
| `deliver` | studio only | `GET /outbox/{id}` (Range resume), verify SHA-256, store at `clip_relative_path`, rewrite `latest.m3u` | `POST /outbox/{id}/ack` |

**Direct push and the process lease.** After the studio answered
`complete: true` to your push, keep listing the lease in heartbeats. The
studio ACKs to the orchestrator, which ends the lease; your next heartbeat
reports it `lost`, which for a pushed clip simply means "finished". If the
lease is still being `renewed` 60 s after the push completed, upload the
clip to the outbox instead.

## 6. Transfers

### 6.1 Downloads (raw, outbox, peer raw)

Plain `GET` with optional `Range: bytes=<start>-`; answers 200 (whole
file), 206 (with `Content-Range`) or 416. Clients send
`Accept-Encoding: identity`. Use `shared.http_transfer.download_file`: it
keeps `<dest>.part`, resumes after drops and only renames after the
SHA-256 matched.

### 6.2 Chunked uploads (proxy, clip, studio push)

Each chunk is a separate `POST` whose body is the raw bytes:

| Header | Meaning |
| --- | --- |
| `Upload-Offset` | Where this chunk starts in the whole file |
| `Upload-Length` | Size of the whole file (same on every chunk) |
| `Upload-SHA256` | SHA-256 of the whole file (same on every chunk) |
| `Lease-Id` | Lease under which the worker uploads (orchestrator endpoints) |
| `Clip-Relative-Path` | Studio push only: where to store the clip |

Answers:

* 200 `{"received_bytes": n, "complete": false}` → send the next chunk at `n`.
* 200 `{"received_bytes": total, "complete": true}` → stored and verified.
* 409 `upload_offset_mismatch` with `details.expected_offset` → continue
  from there (how a crashed upload resumes: start at 0, get corrected).
* 422 `hash_mismatch` → receiver discarded everything.
* 413 `upload_too_large`.

A chunk for a different file (other length or hash) discards an unfinished
one; re-sending a file the receiver already completed answers `complete`.
Client: `shared.http_transfer.upload_file_in_chunks`. Server:
`shared.upload_receiver.ChunkedUploadReceiver` — use it on both the
orchestrator and the studio so they behave identically.

## 7. Endpoint reference

Request/response models are in `shared.models`; the YAML has every field.

### 7.1 Orchestrator

| Method & path | Body → response | Notes |
| --- | --- | --- |
| `GET /health` | → `{"status":"ok"}` | open |
| `POST /intake/evolution-webhook` | `EvolutionWebhook` → `IntakeResponse` | see below |
| `POST /intake/test-upload` | multipart `file`, `sender_name`, `received_as` → `IntakeResponse` | dev mode only |
| `POST /admin/enrollment-tokens` | `CreateEnrollmentTokenRequest` → 201 `EnrollmentTokenCreated` | token shown once |
| `POST /enroll` | `EnrollRequest` → 201 `EnrollResponse` | single use |
| `POST /workers/{worker_id}/heartbeat` | `HeartbeatRequest` → `HeartbeatResponse` | |
| `POST /workers/{worker_id}/next` | `NextTaskRequest` → `NextTaskResponse` | offer only |
| `GET /workers` | → `PeersResponse` | tailnet address + port per worker |
| `POST /jobs/{job_id}/claim` | `ClaimRequest` → `ClaimResponse` | 409 `already_claimed`/`not_claimable` |
| `POST /jobs/{job_id}/lease/renew` | `LeaseRenewRequest` → `LeaseRenewResponse` | 409 `lease_lost` |
| `GET /jobs/{job_id}/raw` | → bytes (Range) | 404 once deleted from the VPS |
| `POST /jobs/{job_id}/holder-confirm` | `HolderConfirmRequest` → `HolderConfirmResponse` | 422 `hash_mismatch` drops you as holder |
| `PUT /jobs/{job_id}/thumbnail` | JPEG body, `Lease-Id` → 204 | before the proxy |
| `POST /jobs/{job_id}/proxy` | chunk → `ChunkAccepted` | ends make_proxy lease |
| `POST /jobs/{job_id}/clip` | chunk → `ChunkAccepted` | outbox route; claimed → uploaded |
| `POST /jobs/{job_id}/delivered` | `DeliveredRequest` → `JobStateResponse` | studio; direct route |
| `GET /review/queue?state=…&limit=…` | → `ReviewQueueResponse` | default `awaiting_review`, oldest first |
| `GET /jobs/{job_id}` | → `Job` | |
| `PATCH /jobs/{job_id}/review` | `ReviewUpdate` → `Job` | see below |
| `POST /jobs/{job_id}/regenerate-proxy` | → 202 `RegenerateProxyResponse` | no state change |
| `GET /teams/{car_number}` | → `Team` | from the Baja app; 404 unknown car |
| `GET /manifest?since=…&prova=…` | → `ManifestResponse` | newest first, latest per prova/car/passagem |
| `GET /outbox/{job_id}` | → bytes (Range) | studio |
| `POST /outbox/{job_id}/ack` | `OutboxAckRequest` → `JobStateResponse` | studio; uploaded → delivered → done |
| `GET /fleet` | → `FleetResponse` | workers, stats, disk, leases, job counts |

**Evolution webhook.** Only `event == "messages.upsert"` from others
(`fromMe == false`) is considered. `shared.models.find_incoming_video`
decides: a `documentMessage` (also inside `documentWithCaptionMessage`)
with a `video/*` mimetype → normal job; a `videoMessage` → job with
`low_res: true` and the bot asks for a resend as document; anything else →
200 `ignored: true`. The raw is fetched from `data.message.mediaUrl`, which
Evolution adds when its S3/MinIO media storage is enabled. A repeated
`data.key.id` → 200 `duplicate: true` with the existing `job_id`. Answer
200 for anything understood so Evolution does not retry forever.

**Review update.** `expected_version` must equal the job's `version`
(else 409 `version_conflict`). `action: save` stores only;
`action: ready` needs state `awaiting_review`; `action: reprocess` needs
`claimed|uploaded|delivered|done`, revokes any process lease and sends the
job back to `ready`. `ready`/`reprocess` require `prova`, `car_number`,
`passagem`, `cut_in_s`, `cut_out_s` to be set after the update. `team_name`
is resolved from `car_number`. Cuts must satisfy `0 ≤ in < out ≤ duration`.

### 7.2 Worker-hosted endpoints (port 8770, tailnet only)

Served by a small HTTP server inside the worker app. No app-level auth:
the tailnet ACL (`tag:worker`, `tag:studio`, `tag:server`) is the
boundary, and nothing here is reachable from the internet.

| Method & path | Role | Behaviour |
| --- | --- | --- |
| `GET /health` | all | `{"status":"ok","worker_id":…,"role":…}` |
| `GET /raw/{job_id}` | master, worker | The held raw, with Range (§6.1). 404 if not held. |
| `POST /receive/{job_id}` | studio | Chunked upload (§6.2) plus `Clip-Relative-Path`. |

Studio `POST /receive/{job_id}`: reject a `Clip-Relative-Path` that fails
`shared.layout.is_safe_relative_path` (400 `validation_error`). On the
completing chunk the clip is moved into place, `latest.m3u` is rewritten,
the studio answers `complete: true` to the worker, and then calls
`POST /jobs/{job_id}/delivered` on the orchestrator (retrying in the
background until it succeeds; repeating it is harmless).

## 8. Folder layouts

### 8.1 Studio box (design §7)

```
<clip root>/                       chosen at studio setup
├── latest.m3u                     newest first, max 50, relative paths
└── <prova-slug>/<car>/<passagem>.mp4
    e.g. enduro/42/3.mp4, suspensao/7/1.mp4
```

Built only by `shared.layout.clip_relative_path` (prova slug: lowercase
ASCII, accents removed, other characters → `-`). `latest.m3u` is extended
M3U from `render_latest_playlist`: `#EXTM3U`, then per clip
`#EXTINF:-1,<Prova> - carro <car> [<team>] - passagem <n>` and the
relative path. Both the clip and the playlist are written to a temporary
name and renamed, so OBS never sees half a file. A reprocessed clip
replaces the old one at the same path.

### 8.2 VPS (`BVP_DATA_DIR`, e.g. `/srv/bvp`)

```
inbox/<job_id>.<ext>      raws until 2 holders confirm (hard cap ~25 GB)
proxies/<job_id>.mp4      served by Caddy as /proxies/<job_id>.mp4
thumbs/<job_id>.jpg       served by Caddy as /thumbs/<job_id>.jpg
outbox/<job_id>.mp4       fallback clips, deleted on studio ACK
db/bvp.sqlite3            jobs DB (WAL mode)
```

`<ext>` is the original extension lowercased (`.mp4`, `.mov`), else
`.bin`. In-progress uploads sit beside their final file as `.part` and
`.part.json`. `Job.proxy_url` / `thumbnail_url` are these Caddy paths,
relative to the orchestrator base URL.

### 8.3 Worker working folder (recommended)

```
raws/<job_id>.<ext>       held raws (served to peers, never evicted)
clips/<job_id>.mp4        rendered clips until delivered
tmp/                      proxies, thumbnails, overlays while rendering
```

## 9. Dev tools

* `uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4`
  — synthetic MP4 with ffmpeg.
* `uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4`
  — serves the file and posts a document-message webhook to
  `http://127.0.0.1:8000` (`--as-video` for a low_res intake).
* `shared.testing.evolution_payloads` — the same payloads for tests.
* `shared.testing.asgi_transport.SyncAsgiTransport` — call a FastAPI app
  from a sync `httpx.Client` in tests (e.g. a fake orchestrator).
* `uv run python tools/generate_openapi.py` — regenerate the YAML after
  changing `tools/openapi_contract/` or `shared.models`.
