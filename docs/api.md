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
| 443 | VPS, public | Caddy: review UI, `/proxies/*`, `/thumbs/*`, `/clip-downloads/*` (outbox clips for reviewers), `POST /enroll`, `POST /intake/evolution-webhook` |
| 8080 | VPS, Tailscale IP only, plain HTTP | Caddy: the worker/studio API and `/inbox/*`, `/outbox/*` files |
| 8000 | VPS, compose network only | orchestrator (uvicorn) |
| 8710 | every master/worker, tailnet | worker file server (`GET /raw/{job_id}`, `GET /clip/{job_id}`) |
| 8720 | studio, tailnet | studio receiver (`POST /receive/{job_id}`) |
| 8700 | every worker/studio, 127.0.0.1 only (not on the tailnet) | worker setup/status page |

## 2. Authentication

| Scheme | Header | Who | Source of truth |
| --- | --- | --- | --- |
| `workerBearer` | `Authorization: Bearer <token>` | master, worker, studio | issued by `POST /enroll`, one per worker |
| `adminBearer` | `Authorization: Bearer <token>` | João / scripts | `BVP_ADMIN_TOKEN` |
| `sessionBearer` | `Authorization: Bearer <session token>` | review UI, for the signed-in user | `POST /auth/login` (§9); users in the orchestrator's DB, role `reviewer` or `admin` |
| `sessionCookie` | `Cookie: bvp_session=<session token>` | the browser, via Caddy `forward_auth` | the cookie the review UI sets; read only by `GET /auth/media` |
| `webhookSecret` | `X-Webhook-Secret: <secret>` | Evolution | `BVP_WEBHOOK_SECRET`, set as a custom header in Evolution's instance webhook |

* The orchestrator checks all of them itself, so everything is testable
  locally without Caddy or Tailscale. The three bearer kinds share the
  `Authorization` header; each route tries the kinds it accepts.
* Users sign in on the review UI's own page (`/review/login`). The UI keeps
  the session in an httpOnly, Secure, SameSite=Lax cookie `bvp_session`
  (path `/`) and sends it to the orchestrator as `sessionBearer`. A session
  ends at logout, after **12 h without a request**, or when an admin
  disables the user or sets a new password.
* Caddy asks for no password. For `/proxies/*` and `/thumbs/*` it calls
  `GET /auth/media` (`forward_auth`, §2.1) with the browser's headers.
* **Roles.** Review routes accept a session of a `reviewer` or an `admin`.
  `/admin/*` routes accept an `admin` session or the admin token (scripts);
  a reviewer's session gets **403** `forbidden`.
* Bearer tokens are worker identity. `/workers/{worker_id}/…` with someone
  else's id → 403. Studio-only endpoints called by another role → 403.
* Open endpoints: `GET /health`, and `POST /enroll` (the one-time token in
  the body is the credential; rate-limited, §2.2).

| Endpoint group | Auth |
| --- | --- |
| `POST /intake/evolution-webhook` | webhookSecret |
| `POST /auth/login` | none (the password is the credential; 20 per source IP per 600 s) |
| `/admin/*`, `POST /intake/test-upload`, `GET /metrics` | sessionBearer (role admin) **or** adminBearer |
| worker loop, `/workers`, `/auth/file`, `/outbox/{id}/ack` | workerBearer |
| `/review/queue`, `PATCH …/review`, `…/regenerate-proxy`, `/teams/*`, `/auth/logout`, `/auth/me` | sessionBearer |
| `GET /auth/media` | sessionCookie |
| `GET /jobs/{job_id}`, `GET /manifest` | sessionBearer **or** workerBearer |
| `GET /fleet`, `GET /jobs/{job_id}/events`, `GET /events`, `GET /provas` | sessionBearer **or** adminBearer |

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
studio workers, and for the master while it holds an `archive_clip` lease
on that job; any other path, a dot-file, `..`, or a method other
than GET/HEAD → 403. Workers never build these URLs themselves: task offers
carry the absolute `download_url` (§5).

**`GET /auth/media`** (public side, `/proxies/*` and `/thumbs/*`): the
same pattern for browsers. Caddy's `forward_auth` passes the browser's own
headers, so the `bvp_session` cookie arrives; a live session → 204, else
401, which Caddy returns to the browser. The session's last-seen time is
written at most once a minute, so the player's Range requests stay cheap.

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
| 401 | `enrollment_token_invalid` | Unknown, used, expired or revoked enrollment token |
| 403 | `forbidden` | Wrong worker id, wrong role (a reviewer on an admin route), or (`/auth/file`) not your file |
| 404 | `not_found` | No such job/worker/team |
| 409 | `illegal_transition` | The state machine forbids it (`details.current`, `details.requested`) |
| 409 | `already_claimed` | Someone else holds the lease for that task |
| 409 | `not_claimable` | Job not in a state for that task, or caller not eligible (e.g. not a holder) |
| 409 | `lease_lost` | Lease expired or revoked; stop the task |
| 409 | `version_conflict` | `expected_version` is stale (`details.current_version`) |
| 409 | `raw_unavailable` | `restore` after the discard grace period, and no worker or inbox still has the raw |
| 409 | `upload_offset_mismatch` | Chunk at the wrong offset (`details.expected_offset`) |
| 409 | `already_exists` | Admin pages: that username, phone, prova name or clip folder is taken |
| 409 | `conflict` | Admin pages: not possible now (last enabled admin, revoking a used token, renaming a synced prova) |
| 413 | `upload_too_large` | Upload over the declared length or the size limit |
| 413 | `inbox_full` | VPS inbox above `BVP_INBOX_CAP_GB` |
| 422 | `hash_mismatch` | SHA-256 of the received/held file is wrong |
| 422 | `missing_review_fields` | `ready`/`reprocess` without all fields (`details.missing`) |
| 422 | `unknown_prova` | `ready`/`reprocess`/`retry`→ready with a prova that is not in `GET /provas` |
| 429 | `rate_limited` | `/enroll` or `/auth/login` over its per-IP limit; see `Retry-After` |
| 503 | `dev_mode_disabled` | `/intake/test-upload` without `BVP_DEV_MODE=1` |
| 503 | `upstream_error` | Tailscale API, Evolution or the Baja results API failed |

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
| awaiting_review / ready → discarded | review `action: discard` (records `discarded_from`) |
| discarded → `discarded_from` | review `action: restore` |
| received / proxy_ready / claimed → failed | `POST /jobs/{id}/failed` (records `failed_from`) |
| failed → received / proxy_ready / ready | review `action: retry` (back to `failed_from`; claimed → ready) |
| failed → discarded | review `action: discard` |

* **`discarded`** can be undone (`restore`, back to `Job.discarded_from`).
  The orchestrator revokes any lease at once, but deletes nothing for the
  **discard grace period** (`BVP_DISCARD_GRACE_HOURS`, default 24 h; the
  end is `Job.discard_grace_ends_at`, fixed at discard time): holders keep
  the raw and the VPS its inbox copy, so a restore just works. After it,
  the job is listed in each holder's heartbeat answer
  (`deletable_job_ids`) and the inbox cleanup drops the VPS copy; restore
  then works only while some copy is left (a confirmed holder that has not
  deleted it yet, or the inbox), else 409 `raw_unavailable`.
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
  else `null`.
* **`version` is the review version.** It goes up by one only when a
  reviewer changes the job: any `PATCH …/review` action (`save`, `ready`,
  `reprocess`, `discard`, `restore`, `retry`), including the state change
  that action causes. Machine activity never bumps it: holder
  confirmations, proxy/thumbnail/media-info uploads, claims, lease
  expiry, clip upload and delivery, sweepers. So a worker busy with the
  job cannot make a reviewer's save fail; only another reviewer's change
  can. (Until 2026-11 it went up on every change, which made every save
  during processing a `version_conflict`.) Watch `state` and the other
  fields, not `version`, to notice machine progress.

## 5. Worker loop

```
first run:   POST /enroll ──▶ worker_id, bearer token, Tailscale key, tailnet_base_url ──▶ tailscale up
every 15 s:  POST /workers/{id}/heartbeat   (renews every lease it lists)
when free:   POST /workers/{id}/next        {accept_kinds: [...]}
             └─ task? ──▶ POST /jobs/{job_id}/claim {task_kind} ──▶ lease + task
                          do the work, then report (table below)
                          cannot succeed? ──▶ POST /jobs/{job_id}/failed
             └─ null  ──▶ wait retry_after_s, ask again
```

* **Where the bearer token goes.** Only to two origins (scheme, host and
  port): the configured `orchestrator_url` and the `tailnet_base_url` from
  the `/enroll` answer (Caddy's tailnet listener, which serves `/inbox/*`
  and `/outbox/*`). Never to a peer (`:8710`), the studio (`:8720`) or any
  other URL an offer might carry.
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
  `deletable_job_ids` lists held raws of jobs discarded longer ago than the
  discard grace period (§4); delete them. Within the grace period they are
  not listed, because a reviewer may still restore the job.
* Report `measurements` (bytes + seconds) in the next heartbeat; the
  orchestrator keeps the rolling `stats`. `held_job_ids` lists every raw on
  your disk so the orchestrator can reconcile holders after crashes.

| Task kind | Who gets it | What to do | How it ends |
| --- | --- | --- | --- |
| `download_raw` | master (always, see backfill below), one more worker; never studio | GET `download_url` with your bearer (Caddy inbox), or the `fallback_download_urls` (peers, port 8710, no token), with `shared.http_transfer.download_file`; ffprobe it | `POST /jobs/{id}/holder-confirm` |
| `make_proxy` | a confirmed holder | Probe (`shared.media_probe`), render thumbnail and proxy with `tone_map_hdr=info.is_hdr`; `PUT …/media-info` (when the task has `media_info_upload_url`), `PUT …/thumbnail`, then chunked `POST …/proxy` | completing chunk of `…/proxy` |
| `process` | a confirmed holder | Render the clip (`build_final_render_command`, overlay PNG by the worker, `tone_map_hdr`). Deliver as the offer's `delivery` says: `direct` → push to `delivery.studio_url` (§7.2), and on failure fall back to the outbox; `outbox` → chunked `POST …/clip` | studio ACK (direct) or completing chunk of `…/clip` (outbox) |
| `deliver` | studio only | GET `download_url` (Caddy outbox) with your bearer, verify SHA-256, store at `clip_relative_path`, rewrite `latest.m3u` | `POST /outbox/{id}/ack` |
| `archive_clip` | master only, lowest priority (after backfill) | Keep one clip version in the archive at `archive_relative_path`: move your own kept copy, else GET the first working `download_urls` entry (peers' `:8710/clip/{job_id}?sha256=…` without token, the Caddy outbox with it), verify SHA-256 | `POST /jobs/{id}/clip-archived` |

**Failures.** `POST /jobs/{id}/failed {lease_id, message, exit_code, scope}`
ends the lease, and that worker is never offered that task on that job
again. `scope` (design §17) says whose fault it is:

| `scope` | Meaning | Examples | Effect |
| --- | --- | --- | --- |
| `worker` (default) | this worker could not do it | disk full, local ffmpeg crash, network gave up | task re-offered to other workers; the job fails only when a **second, different** worker reports `worker` for the same task on this job |
| `job` | the input is bad | hash mismatch at the source (the inbox copy itself is wrong), undecodable raw | the job fails at once |

"Fails" means: a job in `received`, `proxy_ready` or `claimed` goes to
`failed` (with `failed_from`) and its other leases are revoked. In any
other state (a second holder's late download, a proxy re-render after
review) it cannot fail, so it keeps its state. Whenever the job does not
fail, `Job.failure` and `waiting_reason` are set, and a `claimed` job whose
process task failed with scope `worker` goes back to `ready` for another
holder.

**Clips are kept and archived** (contract 1.2.0; design §11 as amended).
After a verified delivery the rendering worker keeps its clip
(`clips/<job_id>.<sha256>.mp4`, §8.3), lists it in every heartbeat's
`held_clips` and serves it on `:8710 GET /clip/{job_id}`. Every clip that
reaches the outbox or the studio is a **clip version** of its job
(`Job.clip_versions`; a reprocess adds version 2, 3, …, never replaces).
The master is offered `archive_clip` for every version it does not have
yet, of any job not discarded, after every other task it accepts
(including raw backfill); sources are the master itself (empty
`download_urls`), online workers that keep that clip (the renderer first),
then the outbox while it has that exact clip. A version with no reachable
source is skipped; one whose copy the master reported failed
(`POST …/failed`) waits 10 minutes, without any exclusion and without
touching the job. The heartbeat answer's `archived_clip_sha256s` lists the
held clips the master has; only those may be evicted, only near the cap
(under 4 GB free), oldest first. **Outbox copies** are deleted only when
the studio ACKed **and** the master archived that clip, or once
`BVP_OUTBOX_KEEP_UNARCHIVED_HOURS` (72) passed since the ACK; a job is
`done` at the ACK either way.

**Where the clip is** (for the review UI): `Job.clip.in_outbox` says the
VPS outbox has the current clip, and `Job.clip.download_url`
(`/clip-downloads/<job_id>.mp4`, relative to the public base URL, reviewer
auth like proxies) is where a reviewer downloads it; `ManifestEntry` and
`JobSummary.clip_download_url` carry the same. `ClipVersion.archived_at`
says whether the master has each version.

**Delivery decision.** Every `process` offer (and claim answer) carries
`delivery: {mode, studio_url}`. The orchestrator says `direct`, with
`studio_url = http://<studio tailnet address>:<receiver_port>/receive/<job_id>`,
when a studio heartbeated within the last 45 s and reported an address;
otherwise `outbox` with `studio_url: null`. Workers do not look at
`GET /workers` to decide.

**Master backfill.** The master must end up holding every raw (design §4),
also those that reached two holders while it was offline. So `/next`
offers the master `download_raw` for every job it does not hold, in any
state except `discarded` (including `done` and `failed`), but only after
every other task it accepts: proxies, processing, and downloads of jobs
that are still in the live pipeline. The source is the VPS inbox while it
still has the raw, else online confirmed holders on `:8710`.

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
| `Frame-Rate` | Proxy upload only, optional: the proxy's frames per second from ffprobe (e.g. `29.97`); stored as `Job.frame_rate` when the upload completes |

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
| `PUT /jobs/{job_id}/media-info` | `ProbeReport` JSON, `Lease-Id` → 204 | make_proxy lease; the raw's ffprobe report (contract 1.2.0, below) |
| `POST /jobs/{job_id}/proxy` | chunk (+ `Frame-Rate`) → `ChunkAccepted` | ends make_proxy lease; sets `Job.frame_rate` |
| `POST /jobs/{job_id}/clip` | chunk → `ChunkAccepted` | outbox route; claimed → uploaded |
| `POST /jobs/{job_id}/delivered` | `DeliveredRequest` → `JobStateResponse` | studio; direct route |
| `POST /jobs/{job_id}/clip-archived` | `ClipArchivedRequest` → `JobStateResponse` | master, `archive_clip` lease; 422 `hash_mismatch` if the sha256 is no version of the job; state unchanged |
| `GET /review/queue?state=…&limit=…` | → `ReviewQueueResponse` | default `awaiting_review`, oldest first |
| `GET /jobs/{job_id}` | → `Job` | |
| `PATCH /jobs/{job_id}/review` | `ReviewUpdate` → `Job` | see below |
| `POST /jobs/{job_id}/regenerate-proxy` | → 202 `RegenerateProxyResponse` | no state change |
| `GET /teams/{car_number}` | → `Team` | the teams table (results API sync + admin corrections); 404 unknown car |
| `GET /provas` | → `ProvasResponse` | synced provas first, then manual; inactive ones flagged |
| `GET /manifest?since=…&prova=…` | → `ManifestResponse` | newest first, latest per prova/car/passagem |
| `POST /outbox/{job_id}/ack` | `OutboxAckRequest` → `JobStateResponse` | studio; uploaded → delivered → done |
| `GET /fleet` | → `FleetResponse` | workers, stats, disk, leases, job counts |
| `GET /jobs/{job_id}/events` | → `EventsResponse` | that job's event log, oldest first; 404 unknown job |
| `GET /events?worker_id=…&since=…&limit=…` | → `EventsResponse` | event log, oldest first; without `since` the newest `limit` (default 100, max 1000) |
| `GET /metrics` | → Prometheus text 0.0.4 | admin only; see below |
| `POST /auth/login` | `LoginRequest` → `LoginResponse` | session token; 401 for any wrong detail; 429 per IP |
| `POST /auth/logout` | → 204 | ends the session |
| `GET /auth/me` | → `CurrentUser` | username and role |
| `GET /auth/media` | cookie → 204 / 401 | Caddy forward_auth (§2.1) |

**Admin routes** (role admin or the admin token; every change is written to
the event log with `details: {before, after}`, readable at `GET
/admin/audit?area=…`, newest first):

| Method & path | Body → response | Notes |
| --- | --- | --- |
| `GET/POST /admin/users`, `PATCH /admin/users/{username}` | `CreateUserRequest`/`UpdateUserRequest` → `User` | never deleted, only disabled; disable or new password ends sessions; 409 for the last admin |
| `GET/POST /admin/contacts`, `PATCH /admin/contacts/{phone}` | `CreateContactRequest`/`UpdateContactRequest` → `Contact` | see "Contacts" below; null clears name, default prova, notes |
| `POST /admin/provas`, `PATCH /admin/provas/{prova_id}`, `PUT /admin/provas/order` | → `Prova` / `ProvasResponse` | manual provas; synced ones only (de)activate |
| `GET /admin/teams`, `PUT/DELETE /admin/teams/{car_number}` | `SaveTeamRequest` → `TeamRow` | on a synced team PUT stores a correction kept across syncs, DELETE removes it |
| `POST /admin/results-sync` | → `ResultsSyncReport` | see "Results API" below; 422 unknown event, 503 API down/limited |
| `GET /admin/enrollment-tokens`, `POST …/{token_id}/revoke` | → `EnrollmentTokensResponse` / `EnrollmentTokenInfo` | unused/used/expired/revoked; 409 for a used one |
| `POST /admin/workers/{worker_id}/revoke` | → `Worker` | its token answers 401, leases end, holder rows dropped |
| `GET/PATCH /admin/settings` | `UpdateSettingsRequest` → `SettingsResponse` | `.env` values are first-start defaults only; secrets only as configured/missing |
| `GET /admin/audit?area=&limit=` | → `EventsResponse` | newest first |

**Contacts.** Every sender of a private message is recorded (status
`unknown`, display name from `pushName`). For a video: `blocked` → 200
ignored, no reply; in intake mode `allowlist`, not `allowed` → 200 ignored
and `SENDER_NOT_AUTHORIZED` at most once per 24 h; otherwise the job is
created with the contact's default prova filled in.

**Results API.** Provas and teams come from the Baja results API
(`GET {base}/resultados/evento.php?evt=` and `equipes.php?evt=`; contract:
`docs/results-api.md` in `baja-sae-brasil-online`) for the event id set in
Settings: on change, `POST /admin/results-sync`, and every 6 h (15 min
after a failure). Provas are keyed by the API's prova id, teams by car
number. A sync adds and renames (a renamed prova relabels its jobs and keeps
its slug), and marks rows the API no longer lists inactive; it never
deletes. A manual prova with the same clip folder as a new synced one is
adopted. `ProcessTask.team_name` (the overlay text) is the team's
`short_name` when it has one; `Job.team_name` keeps the full name.

**Evolution webhook.** The route is exactly **`POST
/intake/evolution-webhook`** with the header **`X-Webhook-Secret:
<BVP_WEBHOOK_SECRET>`** (wrong or missing → 401); Evolution posts it over
the VPS's internal Docker network (no public route), and `infra/vps/up.sh`
configures it as the Baja Bot **instance** webhook (no global webhook; nothing else consumes it).
Only `event == "messages.upsert"` from others (`fromMe == false`) in a
private chat is considered: a `remoteJid` ending in `@g.us` or
`@broadcast`, or a key with a `participant` (group sender), → 200
`ignored: true`, no job and no bot reply (design §3, videos by DM only). `shared.models.find_incoming_video` decides: a
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
is accepted but the bot asks the sender to hold further videos.

**Bot answers** (`orchestrator/domain/bot_replies.py`) are attached to the
video they are about, because volunteers send several files in a row:

| Situation | Reaction on the video | Text (quoted reply to the video) |
| --- | --- | --- |
| verified, job created | ✅ | none |
| arrived as a WhatsApp video (`low_res`) | ⚠️ | `LOW_RES_RESEND_AS_DOCUMENT` |
| inbox above the soft cap | ⚠️ | `INBOX_NEARLY_FULL_PLEASE_HOLD` |
| inbox above the hard cap (413) | ❌ | `INBOX_FULL_RESEND_LATER` |
| download or a hash check failed | ❌ | `DOWNLOAD_FAILED_PLEASE_RESEND` |

A ⚠️ video gets no ✅ later (the warning stays visible); a later ❌
replaces it if the download then fails (WhatsApp keeps one reaction per
message). Reactions go through Evolution `POST /message/sendReaction/{instance}`
with `{"key": {"remoteJid", "fromMe": false, "id"}, "reaction"}`, the key
**exactly as it arrived** (an `@lid` `remoteJid` stays `@lid`: reactions
address the message). Texts go through `sendText` to the phone number
(`sender_jid`, i.e. `remoteJidAlt` for `@lid` senders) with
`"quoted": {"key": <same key>, "message": {"documentMessage"|"videoMessage":
{mimetype, fileName}}}`; sending `message` means Evolution does not need its
own message store to build the quote. Request shapes verified against
Evolution 2.3.7 (`src/validate/message.schema.ts`, `src/api/dto/sendMessage.dto.ts`,
`whatsapp.baileys.service.ts`; see `orchestrator/clients/evolution.py`).
Every text is in `orchestrator/domain/bot_messages.py` (pt-BR).

**Media info** (contract 1.2.0). The first holder already runs ffprobe
on the raw to make the proxy; it sends the whole answer
(`ffprobe -show_format -show_streams`, as `ProbeReport {format, streams,
location_present}`) to the task's `media_info_upload_url` =
`PUT /jobs/{id}/media-info`, under its make_proxy lease and before the
thumbnail. **Location never leaves the worker:** every tag whose name
mentions location/GPS (`location`, `com.apple.quicktime.location.ISO6709`,
…) is removed by `shared.media_details.probe_report_from_ffprobe` and only
`location_present: true` remains; the orchestrator strips them again on
arrival. The report is stored as `Job.media_info` and fills
`Job.frame_rate` while that is still empty (the proxy's `Frame-Rate`
header then replaces it with the proxy's exact rate). Over 512 KB → 413;
not a `ProbeReport` → 400; no live make_proxy lease → 409 `lease_lost`. A
refusal never fails the proxy task; a task without the URL (older
orchestrator) skips the upload. The review UI summarises it under the
player with `shared.media_details.summarize_probe_report`.

**Review update.** `expected_version` must equal the job's `version`
(else 409 `version_conflict`). Since `version` only moves on review
actions (§4), the conflict means another reviewer changed the job.

| `action` | From states | Effect |
| --- | --- | --- |
| `save` | any except `discarded` | store fields/cuts only |
| `ready` | `awaiting_review` | → `ready`; needs all fields |
| `reprocess` | `claimed`, `uploaded`, `delivered`, `done` | → `ready`; revokes any process lease; needs all fields |
| `discard` | `awaiting_review`, `ready`, `failed` | → `discarded`, records `discarded_from`; nothing deleted during the grace period (§4); fields not needed |
| `restore` | `discarded` | → `discarded_from`; 409 `raw_unavailable` after the grace period when no copy of the raw is left; fields not needed |
| `retry` | `failed` | → `failed_from` (`claimed` → `ready`); clears `failure`; needs all fields only when the target is `ready` (may be corrected in the same call) |

Other combinations → 409 `illegal_transition`. "All fields" = `prova`,
`car_number`, `passagem`, `cut_in_s`, `cut_out_s` set after the update,
else 422 `missing_review_fields`. `team_name` is resolved from
`car_number`. Cuts must satisfy `0 ≤ in < out ≤ duration`.

**Metrics.** `GET /metrics` (`adminBearer`, contract 1.1.0) answers in the
Prometheus text format for an external scraper on the tailnet
(`infra/vps/MONITORING.md`). Gauges, read on every scrape:
`bvp_jobs{state}`, `bvp_workers_enrolled{role}`, `bvp_workers_online{role}`
(heartbeat within 45 s), `bvp_storage_bytes{area="inbox"|"proxies"|"outbox"}`
(proxies includes thumbs), `bvp_disk_free_bytes`, `bvp_inbox_hold_bytes`,
`bvp_inbox_cap_bytes`. Counters since the process started (they reset on a
restart, which `rate()`/`increase()` handle): `bvp_task_offers_total{kind}`,
`bvp_task_claims_total{kind}` (new leases only),
`bvp_task_failures_total{kind,scope}`. Every label value is always listed,
zeros included.

### 7.2 Worker-hosted endpoints (tailnet only)

Served by small HTTP servers inside the worker app. No app-level auth in
v1: the tailnet ACL (`tag:worker`, `tag:studio`, `tag:server`) is the
boundary (design §15); short-lived fetch tokens are a later hardening item.

| Port | Method & path | Role | Behaviour |
| --- | --- | --- | --- |
| 8710 | `GET /health` | master, worker | `{"status":"ok","worker_id":…,"role":…}` |
| 8710 | `GET /raw/{job_id}` | master, worker | The held raw, with Range (§6.1). 404 if not held. |
| 8710 | `GET /clip/{job_id}[?sha256=…]` | master, worker | A kept clip (§5 "Clips are kept and archived"), with Range: exactly that version, or the newest. 404 if not kept. |
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

Built from the prova's **stored slug** (`provas.slug`, made by
`shared.layout.slugify_prova` when the prova was created: lowercase ASCII,
accents removed, other characters → `-`) with
`shared.layout.clip_relative_path_in_folder`. A renamed prova keeps its
slug, so its clips stay in the same folder; a prova name not in the table
(older jobs) falls back to the slug of the name. `latest.m3u` is extended
M3U from `render_latest_playlist`: `#EXTM3U`, then per clip
`#EXTINF:-1,<Prova> - carro <car> [<team>] - passagem <n>` and the
relative path. Both are written as dot-files and renamed, so OBS never
sees half a file. A reprocessed clip replaces the old one at the same path
on the box; the master's archive keeps both (`<passagem>.v2.mp4`, built by
`shared.layout.archive_relative_path`), and `ManifestEntry.version` says
which version is current.

### 8.2 VPS (`BVP_DATA_DIR`, default `/srv/pipeline`)

```
inbox/<job_id>.<ext>      raws until 2 holders confirm; Caddy :8080 /inbox/*
proxies/<job_id>.mp4      Caddy :443 /proxies/<job_id>.mp4 (forward_auth → /auth/media)
thumbs/<job_id>.jpg       Caddy :443 /thumbs/<job_id>.jpg (forward_auth → /auth/media)
outbox/<job_id>.mp4       fallback clips; deleted after studio ACK + master archive (or 72 h
                          after the ACK); Caddy :8080 /outbox/* (studio, archiving master)
                          and :443 /clip-downloads/* (signed-in users: the Download button;
                          forward_auth → /auth/media)
db/bvp.sqlite3            jobs DB (WAL mode)
reviewers.txt             optional: imported into the users table on a first start (§9)
```

`<ext>` is the original extension lowercased (`.mp4`, `.mov`), else
`.bin`. In-progress files are hidden dot-files beside their final name.
`Job.proxy_url` / `thumbnail_url` are the Caddy paths, relative to the
public base URL.

### 8.3 Worker working folder (recommended)

```
raws/<job_id>.<ext>       held raws (served on :8710, never evicted, deleted on discard)
clips/<job_id>.mp4        the clip being rendered/delivered (+ .json sidecar)
clips/<job_id>.<sha256>.mp4   delivered clips, kept until archived by the master
                          (served on :8710 /clip; evicted near the cap only once archived)
clips/<prova>/<car>/<passagem>[.vN].mp4   master only: the archive of every clip version
tmp/                      proxies, thumbnails, overlays while rendering
```

## 9. Users and the reviewers file

Users (username, bcrypt hash, role `reviewer`/`admin`, disabled) live in
the orchestrator's DB and are managed on the admin Users page. On the first
start with an **empty** users table, `BVP_REVIEWERS_FILE` (default
`/srv/pipeline/reviewers.txt`) is imported if present: each line becomes a
reviewer with the same password (the hashes are reused) and the user named
in `BVP_BOOTSTRAP_ADMIN` becomes an admin. Afterwards the file is ignored
(logged on every start). Without a file, create the first admin with
`POST /admin/users` and the admin token.

The file keeps its old strict format, parsed by
`shared.reviewers_file.load_reviewers_file`:

```
# Baja review team — one reviewer per line
ana $2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG
```

One `username bcrypt-hash` pair per line, one space; `#` comments; htpasswd
`user:hash` lines, tabs, extra words and duplicates are refused with the
line number, and a file that does not parse stops that first start.

## 10. Orchestrator environment

Every orchestrator setting is `BVP_*` (design §15). The Tailscale minting
variables keep the names fixed in design §8.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BVP_DATA_DIR` | `/srv/pipeline` | Root of the VPS layout (§8.2) |
| `BVP_REVIEWERS_FILE` | `/srv/pipeline/reviewers.txt` | Imported into an empty users table on the first start (§9) |
| `BVP_BOOTSTRAP_ADMIN` | empty | The imported user who becomes admin |
| `BVP_PASSWORD_HASH_ROUNDS` | `12` | bcrypt cost for passwords set on the admin page |
| `BVP_LOGIN_RATE_LIMIT_REQUESTS` / `_WINDOW_S` | `20` / `600` | `/auth/login` attempts per source IP per window |
| `BVP_ADMIN_TOKEN` | — (required) | `adminBearer` token |
| `BVP_WEBHOOK_SECRET` | — (required) | Expected `X-Webhook-Secret` value |
| `BVP_DEV_MODE` | `0` | `1` enables `/intake/test-upload` and serves `/inbox/*`, `/outbox/*`, `/proxies/*`, `/thumbs/*` from the orchestrator (Caddy's rules, Range; not in the OpenAPI document). Refused at startup unless `BVP_PUBLIC_BASE_URL` is localhost |
| `BVP_PORT` | `8000` | Port uvicorn listens on inside the compose network |
| `BVP_PUBLIC_BASE_URL` | — (required) | Public 443 site, e.g. `https://baja.example.org` (proxy/thumbnail links) |
| `BVP_TAILNET_BASE_URL` | — (required) | Caddy tailnet listener, e.g. `http://100.101.102.103:8080`; base of every absolute URL in task offers |
| `BVP_TRUSTED_PROXY_CIDRS` | `127.0.0.1/32,10.201.0.0/24,172.16.0.0/12` | Peers whose `X-Forwarded-For` is trusted (Caddy; `10.201.0.0/24` = the VPS's `baja-edge`) |
| `BVP_ENROLL_RATE_LIMIT_REQUESTS` | `10` | `/enroll` requests allowed per source IP… |
| `BVP_ENROLL_RATE_LIMIT_WINDOW_S` | `600` | …per this many seconds |
| `BVP_INBOX_HOLD_GB` | `22` | Soft cap: above it the bot asks senders to hold. First-start default of the Settings page |
| `BVP_INBOX_CAP_GB` | `25` | Hard cap: above it intake answers 413 `inbox_full` |
| `BVP_PROXY_EVICT_MIN_FREE_GB` | `10` | Evict oldest completed proxies when free disk drops below this. First-start default |
| `BVP_INTAKE_MODE` | `open` | `open` or `allowlist`. First-start default |
| `BVP_INSTALLER_DOWNLOAD_URL` | the repo's `releases/latest` page | Shown with new enrollment tokens. First-start default |
| `BVP_EVENT_ID` | empty | Results API event (e.g. `26BR`). First-start default |
| `BVP_RESULTS_API_URL` | `https://api.bajasaebrasil.net` | Results API base. First-start default |
| `BVP_DISCARD_GRACE_HOURS` | `24` | How long a discarded job's raws are kept (holders and inbox) so `restore` works (§4). First-start default |
| `BVP_OUTBOX_KEEP_UNARCHIVED_HOURS` | `72` | An outbox clip the studio ACKed but the master has not archived is kept this long (master offline). First-start default |
| `BVP_WHATSAPP_MEDIA_BASE_URL` | `https://mmg.whatsapp.net` | Only media URLs under this are fetched (dev: the fake CDN) |
| `BVP_EVOLUTION_URL` | `http://evolution:8080` | Evolution API, for `sendText` replies |
| `BVP_EVOLUTION_API_KEY` | — (required) | Evolution API key (`BVP_EVOLUTION_INSTANCE_TOKEN` is accepted as an alias) |
| `BVP_EVOLUTION_INSTANCE` | `baja-bot` | Evolution instance name |
| `BVP_TEAMS_FILE` | empty | Optional old CSV `car_number,team_name,school`, imported once into an empty teams table on the first start |
| `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_CLIENT_SECRET`, `TAILSCALE_TAILNET`, `TAILSCALE_API_BASE` | see design §8 | Auth-key minting for `/enroll` (`infra/tailnet/mint_auth_key.md`) |

## 11. Dev tools

* `uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4`
  — synthetic MP4 with ffmpeg.
* `uv run python orchestrator/fakes/make_local_workers.py` — mints master,
  worker and studio enrollment tokens from a dev-mode orchestrator and
  writes `.scratch/local/<role>/worker.json` for each (five-terminal local
  run: `worker/README.md`).
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
