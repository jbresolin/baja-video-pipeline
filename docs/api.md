# Orchestrator API contract

Everything the orchestrator, the worker app (roles `master`, `worker`,
`studio`) and the review UI need to agree on. The machine-readable twin of
this page is [`orchestrator/openapi.yaml`](../orchestrator/openapi.yaml),
generated from `tools/openapi_contract/` over the pydantic models in
`shared.models`. **The models are the source of truth for field names and
types**; this page explains the behaviour around them. Design background:
[`design.md`](design.md) (§15 wins over anything older).

## 1. Conventions

| Topic | Rule |
| --- | --- |
| Transport | HTTP/1.1. Workers and the studio reach the orchestrator through Caddy's **tailnet listener** `http://<vps tailscale ip>:8080`. Reviewers, Evolution and `/enroll` use the public 443 site. Locally everything also works on plain `http://127.0.0.1:8000`. |
| Bodies | JSON (`application/json`, UTF-8) unless stated. Chunked uploads use `application/octet-stream`. |
| Unknown fields | Rejected with 400 `validation_error` (all our models use `extra="forbid"`). The Evolution webhook is the one exception. |
| Ids | Opaque strings (the orchestrator uses UUID4 hex). Never parse them. |
| Times | ISO 8601 with timezone, always UTC, e.g. `2026-10-25T14:03:07Z`. |
| Sizes | Integers, bytes. |
| Hashes | SHA-256 as 64 **lowercase** hex characters (`shared.hashing`). |
| Seconds | Float seconds from the start of the **raw** (`cut_in_s`, `duration_s`). The proxy is constant-frame-rate, so proxy time = raw time. |
| Path params | `{job_id}`, `{worker_id}`, `{car_number}`. |
| Partial files | Every writer writes `.<name>.part` (a dot-file) and renames when complete and verified. Caddy never serves dot-files. Helper: `shared.http_transfer.partial_path_for`. |
| Constants | Heartbeat 15 s, lease TTL 45 s, upload chunk 8 MiB, ports: `shared.protocol`. |

### Ports (design §8)

| Port | Where | What |
| --- | --- | --- |
| 443 | VPS, public | Caddy: review UI, `/proxies/*`, `/thumbs/*`, `POST /enroll`, `POST /intake/evolution-webhook` |
| 8080 | VPS, Tailscale IP only, plain HTTP | Caddy: the worker/studio API and `/inbox/*`, `/outbox/*` files |
| 8000 | VPS, compose network only | orchestrator (uvicorn) |
| 8710 | every master/worker, tailnet | worker file server (`GET /raw/{job_id}`) |
| 8720 | studio, tailnet | studio receiver (`POST /receive/{job_id}`) |

## 2. Authentication

| Scheme | Header | Who | Source of truth |
| --- | --- | --- | --- |
| `workerBearer` | `Authorization: Bearer <token>` | master, worker, studio | issued by `POST /enroll`, one per worker |
| `adminBearer` | `Authorization: Bearer <token>` | João / scripts | `BVP_ADMIN_TOKEN` |
| `reviewerBasic` | `Authorization: Basic …` | review UI in the browser | `BVP_REVIEWERS_FILE` (§9), shared with Caddy |
| `webhookSecret` | `X-Webhook-Secret: <secret>` | Evolution | `BVP_WEBHOOK_SECRET`, set as a custom header in Evolution's instance webhook |

* The orchestrator checks all four itself, so everything is testable
  locally without Caddy or Tailscale. Bearer and Basic share the
  `Authorization` header and are told apart by scheme.
* Reviewers: Caddy asks for Basic auth on `/review*`, `/proxies/*` and
  `/thumbs/*` from the same reviewers file and forwards `Authorization`
  to the orchestrator, which checks it again for the API routes.
* Bearer tokens are worker identity. `/workers/{worker_id}/…` with someone
  else's id → 403. Studio-only endpoints called by another role → 403.
* Open endpoints: `GET /health`, and `POST /enroll` (the one-time token in
  the body is the credential; rate-limited, §2.2).

| Endpoint group | Auth |
| --- | --- |
| `POST /intake/evolution-webhook` | webhookSecret |
| `POST /intake/test-upload`, `POST /admin/enrollment-tokens` | adminBearer |
| worker loop, `/workers`, `/auth/file`, `/outbox/{id}/ack` | workerBearer |
| `/review/queue`, `PATCH …/review`, `…/regenerate-proxy`, `/teams/*` | reviewerBasic |
| `GET /jobs/{job_id}`, `GET /manifest` | reviewerBasic **or** workerBearer |
| `GET /fleet` | reviewerBasic **or** adminBearer |

### 2.1 File serving: `GET /auth/file` (Caddy `forward_auth`)

Raws and outbox clips are served **by Caddy**, not by the orchestrator
(design §15): `http://<vps tailscale ip>:8080/inbox/<job_id>.<ext>` and
`…/outbox/<job_id>.mp4`, with HTTP Range. There is no `/jobs/{id}/raw`
or `GET /outbox/{id}` byte endpoint. Before serving, Caddy calls
`GET /auth/file` on the orchestrator with:

* `Authorization`: copied from the worker's request (its bearer token);
* `X-Forwarded-Uri`: the path about to be served, e.g. `/inbox/abc.mp4`;
* `X-Forwarded-Method`: `GET` or `HEAD`.

| Answer | Meaning |
| --- | --- |
| 204 (any 2xx), optional `X-Worker-Id: <id>` header | allowed; Caddy serves the file (and may log the worker id) |
| 401 `unauthenticated` | no or unknown bearer token |
| 403 `forbidden` | known worker, but not for this file |

Rules: `/inbox/<job_id>.<ext>` is allowed while the caller holds a
`download_raw` lease on that job; `/outbox/<job_id>.mp4` is allowed for
studio workers only; any other path, a dot-file, `..`, or a method other
than GET/HEAD → 403. Workers never build these URLs themselves: task offers
carry the absolute `download_url` (§5).

### 2.2 Rate limit on `POST /enroll`

`/enroll` is public, so the **orchestrator** (not Caddy) limits it: at
most **10 requests per source IP per 600 s** (`BVP_ENROLL_RATE_LIMIT_REQUESTS`,
`BVP_ENROLL_RATE_LIMIT_WINDOW_S`). Every request counts, successful or not.
Over the limit → **429 `rate_limited`** with a `Retry-After: <seconds>`
header. The source IP is the last `X-Forwarded-For` entry when the request
comes from a trusted proxy (`BVP_TRUSTED_PROXY_CIDRS`, i.e. Caddy), else
the TCP peer address.

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
| 403 | `forbidden` | Wrong worker id, wrong role, or (`/auth/file`) not your file |
| 404 | `not_found` | No such job/worker/team |
| 409 | `illegal_transition` | The state machine forbids it (`details.current`, `details.requested`) |
| 409 | `already_claimed` | Someone else holds the lease for that task |
| 409 | `not_claimable` | Job not in a state for that task, or caller not eligible (e.g. not a holder) |
| 409 | `lease_lost` | Lease expired or revoked; stop the task |
| 409 | `version_conflict` | `expected_version` is stale (`details.current_version`) |
| 409 | `upload_offset_mismatch` | Chunk at the wrong offset (`details.expected_offset`) |
| 413 | `upload_too_large` | Upload over the declared length or the size limit |
| 413 | `inbox_full` | VPS inbox above `BVP_INBOX_CAP_GB` |
| 422 | `hash_mismatch` | SHA-256 of the received/held file is wrong |
| 422 | `missing_review_fields` | `ready`/`reprocess` without all fields (`details.missing`) |
| 429 | `rate_limited` | `/enroll` over its per-IP limit; see `Retry-After` |
| 503 | `dev_mode_disabled` | `/intake/test-upload` without `BVP_DEV_MODE=1` |
| 503 | `upstream_error` | Tailscale API, Evolution or Baja app failed |

Caddy itself answers 416 for an unsatisfiable Range on `/inbox/*` and
`/outbox/*` (plain HTTP semantics, no JSON body).

## 4. Job lifecycle

States and allowed moves live in `shared.state_machine`
(`validate_transition` raises `IllegalTransitionError` otherwise).

```
received → proxy_ready → awaiting_review → ready → claimed (lease) → uploaded → delivered → done
    │           │                 │               │        │
    └───────────┴─────────────────┼───────────────┼────────┴── failed ──► failed_from (retry) | discarded
                                  └── discarded ◄─┘

proxy_ready → awaiting_review is automatic.
back to ready also from: claimed (lease expired/revoked), and claimed | uploaded |
delivered | done (review action=reprocess).
direct push: the studio ACK moves claimed → uploaded → delivered in one call.
```

| Move | Triggered by |
| --- | --- |
| received → proxy_ready → awaiting_review | completing chunk of `POST …/proxy` |
| awaiting_review → ready | review `action: ready` |
| ready → claimed | `POST …/claim` with `task_kind: process` |
| claimed → uploaded | completing chunk of `POST …/clip` (outbox route) |
| uploaded → delivered → done | `POST /outbox/{id}/ack`, or `POST …/delivered` (direct route, also from claimed) |
| claimed → ready | process lease expired or revoked |
| claimed / uploaded / delivered / done → ready | review `action: reprocess` |
| awaiting_review / ready → discarded | review `action: discard` |
| received / proxy_ready / claimed → failed | `POST /jobs/{id}/failed` (records `failed_from`) |
| failed → received / proxy_ready / ready | review `action: retry` (back to `failed_from`; claimed → ready) |
| failed → discarded | review `action: discard` |

* **`discarded`** is final. The orchestrator revokes any lease, deletes
  the VPS inbox copy and lists the job in each holder's heartbeat answer
  (`deletable_job_ids`), so holders may delete their raw.
* **`failed`**: `Job.failure` holds who failed, under which task, the
  message and exit code; `Job.failed_from` the state it failed from. The
  review queue shows it (`failure_message`). `retry` returns the job to
  `failed_from` (a job that failed while `claimed` goes to `ready`) and
  clears both.
* **Holders** are tracked beside the state (`Job.holders`): the raw is
  downloaded by the master plus one more worker while the job moves on.
  The first holder to confirm gets `make_proxy`, so the job can reach
  `proxy_ready` before the second holder is done. When the second holder
  confirms, the VPS deletes its copy (`raw_on_vps=false`).
* **Proxy availability** (`proxy_available`) is also beside the state:
  eviction and `regenerate-proxy` never change the state.
* `waiting_reason` is a human sentence for the UI when a job is stuck,
  else `null`. `version` increases on every change of the job.

## 5. Worker loop

```
first run:   POST /enroll ──▶ worker_id, bearer token, Tailscale key ──▶ tailscale up
every 15 s:  POST /workers/{id}/heartbeat   (renews every lease it lists)
when free:   POST /workers/{id}/next        {accept_kinds: [...]}
             └─ task? ──▶ POST /jobs/{job_id}/claim {task_kind} ──▶ lease + task
                          do the work, then report (table below)
                          cannot succeed? ──▶ POST /jobs/{job_id}/failed
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
  task and discard its partial output. A lease also ends normally when you
  report the task's result.
* The heartbeat answer's `accepting_new_raws: false` means you are near
  your storage cap (no new `download_raw`, keep processing what you hold);
  `deletable_job_ids` lists held raws of discarded jobs you may delete.
* Report `measurements` (bytes + seconds) in the next heartbeat; the
  orchestrator keeps the rolling `stats`. `held_job_ids` lists every raw on
  your disk so the orchestrator can reconcile holders after crashes.

| Task kind | Who gets it | What to do | How it ends |
| --- | --- | --- | --- |
| `download_raw` | master (always), one more worker; never studio | GET `download_url` with your bearer (Caddy inbox), or the `fallback_download_urls` (peers, port 8710, no token), with `shared.http_transfer.download_file`; ffprobe it | `POST /jobs/{id}/holder-confirm` |
| `make_proxy` | a confirmed holder | Probe (`shared.media_probe`), render thumbnail and proxy with `tone_map_hdr=info.is_hdr`; `PUT …/thumbnail`, then chunked `POST …/proxy` | completing chunk of `…/proxy` |
| `process` | a confirmed holder | Render the clip (`build_final_render_command`, overlay PNG by the worker, `tone_map_hdr`). Push it to the studio (§7.2); if that fails, chunked `POST …/clip` to the outbox | studio ACK (direct) or completing chunk of `…/clip` (outbox) |
| `deliver` | studio only | GET `download_url` (Caddy outbox) with your bearer, verify SHA-256, store at `clip_relative_path`, rewrite `latest.m3u` | `POST /outbox/{id}/ack` |

**Failures.** `POST /jobs/{id}/failed {lease_id, message, exit_code}` is
for errors that will not go away by retrying the same input (corrupt raw,
ffmpeg exits with an error). A job in `received`, `proxy_ready` or
`claimed` goes to `failed` (with `failed_from`); its other leases are
revoked. In any other state (a second holder's late download, a proxy
re-render after review) the job keeps its state, `Job.failure` and
`waiting_reason` are set, and that task is not offered to the same worker
for that job again.
Network trouble is not a failure: retry, or let the lease expire.

**Direct push and the process lease.** After the studio answered
`complete: true` to your push, keep listing the lease in heartbeats. The
studio ACKs to the orchestrator, which ends the lease; your next heartbeat
reports it `lost`, which for a pushed clip simply means "finished". If the
lease is still being `renewed` 60 s after the push completed, upload the
clip to the outbox instead.

## 6. Transfers

### 6.1 Downloads (inbox, outbox, peer raws)

Plain `GET` of an absolute `download_url` with optional
`Range: bytes=<start>-`; answers 200, 206 (with `Content-Range`) or 416.
Send your bearer token to Caddy (`:8080`); peers (`:8710`) need none.
Clients send `Accept-Encoding: identity`. Use
`shared.http_transfer.download_file`: it keeps the hidden
`.<name>.part`, resumes after drops and only renames after the SHA-256
matched.

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
Partial data lives in hidden `.<name>.part` / `.<name>.part.json` files.
Client: `shared.http_transfer.upload_file_in_chunks`. Server:
`shared.upload_receiver.ChunkedUploadReceiver` — use it on both the
orchestrator and the studio so they behave identically.

### 6.3 WhatsApp media (intake only)

The webhook carries no file. The orchestrator streams the **encrypted**
file from the WhatsApp CDN and decrypts it on the fly with
`shared.whatsapp_media.download_incoming_media` (reference: Baileys
`downloadContentFromMessage`):

1. URL: the message's `url` if it starts with `BVP_WHATSAPP_MEDIA_BASE_URL`
   (default `https://mmg.whatsapp.net`), else that base + `directPath`.
   Nothing else is fetched, so a forged webhook cannot point us elsewhere.
2. Keys: HKDF-SHA256 over `mediaKey`, info `WhatsApp Document Keys`
   (`WhatsApp Video Keys` for a `videoMessage`), 112 bytes →
   iv (16) | cipherKey (32) | macKey (32) | refKey (32).
3. The download is AES-256-CBC ciphertext + a 10-byte MAC
   (first 10 bytes of HMAC-SHA256(macKey, iv + ciphertext)).
4. Checks, all required: `fileEncSha256` = SHA-256 of the whole download;
   the MAC; PKCS7 padding; `fileSha256` = SHA-256 of the plaintext (this
   becomes the job's `sha256`); `fileLength` = plaintext size.

A network error restarts from byte 0 (up to 3 attempts); a failed check
never retries. Plaintext goes to `inbox/.<job_id>.<ext>.part` and is renamed
only after every check passed.

## 7. Endpoint reference

Request/response models are in `shared.models`; the YAML has every field.

### 7.1 Orchestrator

| Method & path | Body → response | Notes |
| --- | --- | --- |
| `GET /health` | → `{"status":"ok"}` | open |
| `POST /intake/evolution-webhook` | `EvolutionWebhook` → 202/200 `IntakeResponse` | header `X-Webhook-Secret`; see below |
| `POST /intake/test-upload` | multipart `file`, `sender_name`, `received_as` → `IntakeResponse` | dev mode only, synchronous |
| `POST /admin/enrollment-tokens` | `CreateEnrollmentTokenRequest` → 201 `EnrollmentTokenCreated` | token shown once |
| `POST /enroll` | `EnrollRequest` → 201 `EnrollResponse` | public, single use, 429 per IP (§2.2) |
| `POST /workers/{worker_id}/heartbeat` | `HeartbeatRequest` → `HeartbeatResponse` | |
| `POST /workers/{worker_id}/next` | `NextTaskRequest` → `NextTaskResponse` | offer only; carries `download_url` |
| `GET /workers` | → `PeersResponse` | tailnet address, file server + receiver port |
| `GET /auth/file` | headers → 204 / 401 / 403 | Caddy forward_auth (§2.1) |
| `POST /jobs/{job_id}/claim` | `ClaimRequest` → `ClaimResponse` | 409 `already_claimed`/`not_claimable` |
| `POST /jobs/{job_id}/lease/renew` | `LeaseRenewRequest` → `LeaseRenewResponse` | 409 `lease_lost` |
| `POST /jobs/{job_id}/failed` | `FailedRequest` → `JobStateResponse` | ends the lease (§5) |
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
| `POST /outbox/{job_id}/ack` | `OutboxAckRequest` → `JobStateResponse` | studio; uploaded → delivered → done |
| `GET /fleet` | → `FleetResponse` | workers, stats, disk, leases, job counts |

**Evolution webhook.** The route is exactly **`POST
/intake/evolution-webhook`** with the header **`X-Webhook-Secret:
<BVP_WEBHOOK_SECRET>`** (wrong or missing → 401); the VPS Caddyfile routes
exactly that path from the public site, and it is configured as the Baja
Bot **instance** webhook (no global webhook; nothing else consumes it).
Only `event == "messages.upsert"` from others (`fromMe == false`) is
considered. `shared.models.find_incoming_video` decides: a
`documentMessage` (also inside `documentWithCaptionMessage`) with a
`video/*` mimetype → normal job; a `videoMessage` → `low_res: true` and the
bot asks for a resend as document; anything else → 200 `ignored: true`.
The fields read are `url`, `directPath`, `mediaKey`, `fileEncSha256`,
`fileSha256`, `fileLength`, `mimetype`, `fileName`; binary ones arrive as
base64 text (Node's `{"type":"Buffer","data":[…]}` and index-keyed objects
are accepted too). `mediaUrl` is not used. The answer comes fast: 202
`accepted: true, job_id: null`; the media is then downloaded and decrypted
in the background (§6.3), and the job is created in `received` only after
every check passed. If that fails, no job exists and the bot asks the
sender to resend. A repeated `data.key.id` (in progress or done) → 200
`duplicate: true` with the existing `job_id` if any. Inbox above
`BVP_INBOX_CAP_GB` → 413 `inbox_full`; above `BVP_INBOX_HOLD_GB` the media
is accepted but the bot asks the sender to hold further videos. Replies to
the sender go through Evolution `sendText`.

**Review update.** `expected_version` must equal the job's `version`
(else 409 `version_conflict`).

| `action` | From states | Effect |
| --- | --- | --- |
| `save` | any except `discarded` | store fields/cuts only |
| `ready` | `awaiting_review` | → `ready`; needs all fields |
| `reprocess` | `claimed`, `uploaded`, `delivered`, `done` | → `ready`; revokes any process lease; needs all fields |
| `discard` | `awaiting_review`, `ready`, `failed` | → `discarded` (final); fields not needed |
| `retry` | `failed` | → `failed_from` (`claimed` → `ready`); clears `failure`; needs all fields only when the target is `ready` (may be corrected in the same call) |

Other combinations → 409 `illegal_transition`. "All fields" = `prova`,
`car_number`, `passagem`, `cut_in_s`, `cut_out_s` set after the update,
else 422 `missing_review_fields`. `team_name` is resolved from
`car_number`. Cuts must satisfy `0 ≤ in < out ≤ duration`.

### 7.2 Worker-hosted endpoints (tailnet only)

Served by small HTTP servers inside the worker app. No app-level auth in
v1: the tailnet ACL (`tag:worker`, `tag:studio`, `tag:server`) is the
boundary (design §15); short-lived fetch tokens are a later hardening item.

| Port | Method & path | Role | Behaviour |
| --- | --- | --- | --- |
| 8710 | `GET /health` | master, worker | `{"status":"ok","worker_id":…,"role":…}` |
| 8710 | `GET /raw/{job_id}` | master, worker | The held raw, with Range (§6.1). 404 if not held. |
| 8720 | `GET /health` | studio | as above |
| 8720 | `POST /receive/{job_id}` | studio | Chunked upload (§6.2) plus `Clip-Relative-Path`. |

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
relative path. Both are written as dot-files and renamed, so OBS never
sees half a file. A reprocessed clip replaces the old one at the same path.

### 8.2 VPS (`BVP_DATA_DIR`, default `/srv/pipeline`)

```
inbox/<job_id>.<ext>      raws until 2 holders confirm; Caddy :8080 /inbox/*
proxies/<job_id>.mp4      Caddy :443 /proxies/<job_id>.mp4 (reviewer Basic auth)
thumbs/<job_id>.jpg       Caddy :443 /thumbs/<job_id>.jpg (reviewer Basic auth)
outbox/<job_id>.mp4       fallback clips, deleted on studio ACK; Caddy :8080 /outbox/*
db/bvp.sqlite3            jobs DB (WAL mode)
reviewers.txt             default BVP_REVIEWERS_FILE (§9)
```

`<ext>` is the original extension lowercased (`.mp4`, `.mov`), else
`.bin`. In-progress files are hidden dot-files beside their final name.
`Job.proxy_url` / `thumbnail_url` are the Caddy paths, relative to the
public base URL.

### 8.3 Worker working folder (recommended)

```
raws/<job_id>.<ext>       held raws (served on :8710, never evicted, deleted on discard)
clips/<job_id>.mp4        rendered clips until delivered
tmp/                      proxies, thumbnails, overlays while rendering
```

## 9. Reviewers file (`BVP_REVIEWERS_FILE`)

Default `/srv/pipeline/reviewers.txt`. Read by **both** Caddy (the
Caddyfile `import`s it inside `basic_auth`) and the orchestrator
(`shared.reviewers_file.load_reviewers_file`), so it has one strict format:

```
# Baja review team — one reviewer per line
ana $2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG
bruno $2y$05$GSNSWdsjEMHlNpC4bm6t1.u9AYqnyN9YhH8lkHrQV8.mcbv7JJ1Wa
```

* One `username bcrypt-hash` pair per line, separated by **one space**.
* A line whose first non-blank character is `#` is a comment; blank lines
  are ignored; whitespace at either end of a line (and `\r`) is ignored.
* Usernames: letters, digits and `. _ @ -`. Hashes: bcrypt
  `$2a$`/`$2b$`/`$2y$` (`caddy hash-password`, or `htpasswd -nbB user pw`
  keeping only the part after the colon).
* Real htpasswd `user:hash` lines are **refused** (Caddy cannot import
  them), as are tabs, extra words or trailing comments, plain-text
  passwords and duplicate usernames. Each error names the line number.
* The username becomes `Job.reviewed_by`. Reload: restart the orchestrator
  and reload Caddy after editing.

## 10. Orchestrator environment

Every orchestrator setting is `BVP_*` (design §15). The Tailscale minting
variables keep the names fixed in design §8.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BVP_DATA_DIR` | `/srv/pipeline` | Root of the VPS layout (§8.2) |
| `BVP_REVIEWERS_FILE` | `/srv/pipeline/reviewers.txt` | Reviewer credentials (§9) |
| `BVP_ADMIN_TOKEN` | — (required) | `adminBearer` token |
| `BVP_WEBHOOK_SECRET` | — (required) | Expected `X-Webhook-Secret` value |
| `BVP_DEV_MODE` | `0` | `1` enables `/intake/test-upload` |
| `BVP_PORT` | `8000` | Port uvicorn listens on inside the compose network |
| `BVP_PUBLIC_BASE_URL` | — (required) | Public 443 site, e.g. `https://baja.example.org` (proxy/thumbnail links) |
| `BVP_TAILNET_BASE_URL` | — (required) | Caddy tailnet listener, e.g. `http://100.101.102.103:8080`; base of every absolute URL in task offers |
| `BVP_TRUSTED_PROXY_CIDRS` | `127.0.0.1/32,172.16.0.0/12` | Peers whose `X-Forwarded-For` is trusted (Caddy) |
| `BVP_ENROLL_RATE_LIMIT_REQUESTS` | `10` | `/enroll` requests allowed per source IP… |
| `BVP_ENROLL_RATE_LIMIT_WINDOW_S` | `600` | …per this many seconds |
| `BVP_INBOX_HOLD_GB` | `22` | Soft cap: above it the bot asks senders to hold |
| `BVP_INBOX_CAP_GB` | `25` | Hard cap: above it intake answers 413 `inbox_full` |
| `BVP_PROXY_EVICT_MIN_FREE_GB` | `10` | Evict oldest completed proxies when free disk drops below this |
| `BVP_WHATSAPP_MEDIA_BASE_URL` | `https://mmg.whatsapp.net` | Only media URLs under this are fetched (dev: the fake CDN) |
| `BVP_EVOLUTION_URL` | `http://evolution:8080` | Evolution API, for `sendText` replies |
| `BVP_EVOLUTION_API_KEY` | — (required) | Evolution API key |
| `BVP_EVOLUTION_INSTANCE` | `baja-bot` | Evolution instance name |
| `BVP_BAJA_APP_URL` | — (required) | Baja app base URL for `/teams/{car_number}` |
| `BVP_BAJA_APP_TOKEN` | empty | Token for the Baja app, if it needs one |
| `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_CLIENT_SECRET`, `TAILSCALE_TAILNET`, `TAILSCALE_API_BASE` | see design §8 | Auth-key minting for `/enroll` (`infra/tailnet/mint_auth_key.md`) |

## 11. Dev tools

* `uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4`
  — synthetic MP4 with ffmpeg.
* `uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4`
  — encrypts the file like WhatsApp, serves the blob as a fake CDN on
  `:8790`, and posts a document-message webhook to `http://127.0.0.1:8000`
  (`--as-video` for a low_res intake). Run the orchestrator with
  `BVP_WHATSAPP_MEDIA_BASE_URL=http://127.0.0.1:8790`.
* `shared.testing.evolution_payloads` and
  `shared.testing.whatsapp_media_encrypt` — the same payloads and
  encryption for tests.
* `shared.testing.asgi_transport.SyncAsgiTransport` — call a FastAPI app
  from a sync `httpx.Client` in tests (e.g. a fake orchestrator).
* `uv run python tools/generate_openapi.py` — regenerate the YAML after
  changing `tools/openapi_contract/` or `shared.models`.
