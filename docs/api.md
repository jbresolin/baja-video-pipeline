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
| Unknown fields | In a request: rejected with 400 `validation_error` (request models derive from `ReceivedByServer`, `extra="forbid"`). In an answer: ignored by every client (answer models derive from `ReadByClients`, `extra="ignore"`), so adding a field to an answer breaks no client from contract 2.4.0 on (design §14). The Evolution webhook is the one exception. |
| Unknown values | The answers workers act on accept a value a client does not know yet as a plain string (contract 2.6.1): `HeartbeatResponse.lease_status` values and `role`, `JobStateResponse.state`. Clients act only on values they know. Any other answer a worker cannot read becomes an `unreadable_answer` API error on the worker side (`worker.api_client.read_answer`), handled like a refused call. |
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
| 443 | VPS, public | Caddy: review UI, `/proxies/*`, `/thumbs/*`, `/clip-downloads/*` (outbox clips for reviewers), `/original-downloads/*` (originals reviewers asked for), `POST /enroll`, `POST /intake/evolution-webhook` |
| 8080 | VPS, Tailscale IP only, plain HTTP | Caddy: the worker/studio API and `/inbox/*`, `/outbox/*` files |
| 8000 | VPS, compose network only | orchestrator (uvicorn) |
| 8710 | every master/worker, tailnet | worker file server (`GET /raw/{job_id}`, `GET /clip/{job_id}`) |
| 8720 | studio, tailnet | studio receiver (`POST /receive/{job_id}`) |
| 8700 | every worker/studio, 127.0.0.1 only (not on the tailnet) | worker setup/status page and the localhost JSON API for the tray icon (§7.3) |

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
| `/review/queue`, `/review/car-videos`, `GET /jobs/{job_id}/sender-messages`, `GET /jobs/{job_id}/pass-preview`, `PUT/DELETE /jobs/{job_id}/presence`, `PATCH …/review`, `…/regenerate-proxy`, `GET/POST …/original`, `/teams/*`, `/auth/logout`, `/auth/me` | sessionBearer |
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
| received → discarded | intake, at job creation, for a duplicate video (same raw SHA-256 as a job not discarded; records `discarded_from: received` and `duplicate_of`; LIVESTACK-51) |
| received / proxy_ready / claimed → failed | `POST /jobs/{id}/failed` (records `failed_from`) |
| failed → received / proxy_ready / ready | review `action: retry` (back to `failed_from`; claimed → ready) |
| failed → discarded | review `action: discard` |

* **`discarded`** can be undone (`restore`, back to `Job.discarded_from`).
  The orchestrator revokes any lease at once, but deletes nothing for the
  **discard grace period** (admin Settings `discard_grace_hours`, default 24 h; the
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
* **Pause.** `paused: true` in the heartbeat means the volunteer paused
  this worker (tray icon or status page, worker/README.md). The worker
  finishes the task it is on, then asks for nothing new, but keeps
  heartbeating (its lease is renewed until that task ends) and keeps
  serving held raws on `:8710`. The orchestrator offers a paused worker
  nothing (`/next` answers `task: null`; `/claim` → 409 `not_claimable`)
  and treats it like an offline worker when choosing the second holder,
  deciding whether the master still counts as a future holder, and
  picking the studio for direct pushes (a paused studio → `outbox`). It
  stays a download source for peers. `GET /fleet` lists `paused` per
  worker; the fleet page shows "pausado". The flag lasts until a heartbeat
  says `paused: false` ("Retomar"); the worker keeps it across restarts.
* **Role.** Send `role` (the role you run as) in every heartbeat; the
  answer then carries `role`, the role the orchestrator has for you. They
  differ only after an admin switched master and worker ("Tornar master",
  §7.1): save the new role and restart (worker/README.md). A heartbeat
  without `role` gets no `role` key at all, because workers before
  LIVESTACK-42 refuse unknown fields in the answer. The reverse is not
  covered: a new worker's `role` is refused (400) by an orchestrator older
  than this, so upgrade the orchestrator first.
* Report `measurements` (bytes + seconds) in the next heartbeat; the
  orchestrator keeps the rolling `stats`. `held_job_ids` lists every raw on
  your disk so the orchestrator can reconcile holders after crashes.
* **Render features** (contract 2.6.0, LIVESTACK-50). Declare what your
  version can render: `capabilities.render_features` at `/enroll`, and
  `render_features` in every heartbeat (it replaces the stored list; left
  out = none). Today `framing` (turning the picture and fitting one that
  is not 16:9 into 1920×1080) and `no_audio` ("Sem áudio"); names are
  plain strings, unknown ones are kept and never required. Every
  `process` task lists `required_render_features`, and `/next` and
  `/claim` hand a render only to a worker that declared all of them (409
  `not_claimable` otherwise, naming what is missing). A job needs
  `framing` when `ProcessTask.rotation` is not 0 or its picture
  (`Job.display_width/height`, after the file's rotation note) is not
  16:9, and `no_audio` when `no_audio` is set on a raw with sound. When
  holders are online but none can render the job, `waiting_reason` is
  "waiting for a holder of the raw that can render these options (a
  worker updated to 0.4.0 or later)". An orchestrator older than 2.6.0
  answers 400 `validation_error` (naming `render_features`) to the field:
  send the beat again **without the key** (not `null`) and leave it out
  for 5 minutes. Deploy the orchestrator first.
  Contract 2.7.0 (LIVESTACK-47, installer 0.5.0) adds `pass_result`: a
  task needs it when it carries `pass_result_text` (the overlay's result
  pill, "Pass results" in §7.1). When holders that could render
  everything else are online but none declared `pass_result`,
  `waiting_reason` is "waiting for a holder of the raw that can draw the
  pass result (a worker updated to 0.5.0 or later)".
  Contract 2.8.0 (LIVESTACK-48, installer 0.6.0) adds `clock`: a task
  needs it when it carries `pass_clock` (the running clock, "Running
  clock" in §7.1). When holders that could render everything else are
  online but none declared `clock`, `waiting_reason` is "waiting for a
  holder of the raw that can draw the running clock (a worker updated to
  0.6.0 or later)".

| Task kind | Who gets it | What to do | How it ends |
| --- | --- | --- | --- |
| `download_raw` | master (always, see backfill below), one more worker; never studio | GET `download_url` with your bearer (Caddy inbox), or the `fallback_download_urls` (peers, port 8710, no token), with `shared.http_transfer.download_file`; ffprobe it | `POST /jobs/{id}/holder-confirm` |
| `make_proxy` | a confirmed holder | Probe (`shared.media_probe`), render thumbnail and proxy with `tone_map_hdr=info.is_hdr`; `PUT …/media-info` (when the task has `media_info_upload_url`), `PUT …/thumbnail`, then chunked `POST …/proxy` | completing chunk of `…/proxy` |
| `process` | a confirmed holder that declared the task's `required_render_features` | Render the clip (`build_final_render_command`, overlay PNG by the worker, `tone_map_hdr`; `rotation`, `framing`, `crop_position` and `no_audio` through `shared.framing.plan_frame`, below; `pass_result_text`, when set, as a third pill under the other two; `pass_clock`, when set, as the running clock beside it: its pill from the largada on, the digits by `drawtext` from the frame time, `shared.ffmpeg_clock`). Report what it was rendered from (`PUT …/render-inputs`, below). Deliver as the offer's `delivery` says: `direct` → push to `delivery.studio_url` (§7.2), and on failure fall back to the outbox; `outbox` → chunked `POST …/clip` | studio ACK (direct) or completing chunk of `…/clip` (outbox) |
| `deliver` | studio only | GET `download_url` (Caddy outbox) with your bearer, verify SHA-256, store at `clip_relative_path`, rewrite `latest.m3u` | `POST /outbox/{id}/ack` |
| `upload_original` | a confirmed holder that is online, the master first (LIVESTACK-41 item 15) | A reviewer asked for the original: chunked upload of the held raw to `upload_url` (`POST /jobs/{id}/original-upload`, `Lease-Id`, `Upload-SHA256` = the job's sha256). Offered right after proxies. A failure report never fails the job; that holder is just not asked again for it | completing chunk of `…/original-upload` |
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

**Clips are kept and archived** (contract 2.1.0; design §11 as amended).
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
`outbox_keep_unarchived_hours` (admin Settings, 72) passed since the ACK; a job is
`done` at the ACK either way.

**Where the clip is** (for the review UI): `Job.clip.in_outbox` says the
VPS outbox has the current clip, and `Job.clip.download_url`
(`/clip-downloads/<job_id>.mp4`, relative to the public base URL, reviewer
auth like proxies) is where a reviewer downloads it; `ManifestEntry` and
`JobSummary.clip_download_url` carry the same. `ClipVersion.archived_at`
says whether the master has each version.

**Render inputs** (contract 2.4.0, LIVESTACK-43). Every clip version
remembers what it was rendered from, so the review UI can say when a clip
on air no longer matches the review (a reviewer edited the job after
delivery). `ClipVersion.render_inputs` (`RenderInputs`): raw sha256, cuts,
prova, car, passagem, show-pass flag, the Settings' name choices
(`team_name_style`, `school_name_style`), the names the overlay shows
(`overlay_team_name`, `overlay_school_name`), the overlay theme version
(SHA-256 of the worker's vendored `tokens.css`) and
`reported_by_worker`. Where they come from:

1. At every `process` claim the orchestrator snapshots the task it hands
   out, with the name choices in force (workers only get the chosen
   names).
2. After rendering and **before** uploading or pushing, the worker sends
   `PUT /jobs/{id}/render-inputs` (`RenderInputsReport`, header
   `Lease-Id` = its process lease): the values it rendered, the overlay
   texts as drawn, its theme version and the clip's `clip_sha256`. 204;
   422 `hash_mismatch` if `raw_sha256` is not the job's; no live process
   lease → 409 `lease_lost`. It never changes the state or `Job.version`.
   A worker that gets an error (an older orchestrator answers 404)
   delivers anyway.
3. When the clip becomes a version (outbox upload complete, or the
   studio's ACK), it keeps the report whose `clip_sha256` is that clip,
   else the snapshot (`reported_by_worker: false`, no theme version).
   Versions from before 2.4.0 have `render_inputs: null`.

Render options (contract 2.6.0): the inputs also hold `rotation`,
`framing`, `crop_position` and `no_audio`. The report carries the task's
options as used; the orchestrator records framing and band **as they
show** (`shared.framing.effective_framing`: none for a picture that is
16:9 after the turn, the band only for `crop`). Inputs recorded before
2.6.0 have `rotation`/`no_audio` null, compared as 0/false.

Pass results (contract 2.7.0, LIVESTACK-47): the inputs also hold
`pass_result_text`, the result line as drawn (null = no pill; also what
inputs recorded before 2.7.0 mean). In `now` it is the line built from the
**latest results fetched**, when the job's toggle shows it, so a result
published or corrected by the judges after the clip was made, or the
toggle changed, lists `pass_result` in `changed`. A failed fetch keeps the
last good answer, so it never changes the comparison. Nothing is
reprocessed automatically.

Running clock (contract 2.8.0, LIVESTACK-48): the inputs also hold
`pass_clock` (`RenderClock`: `largada_s`, `official_units`, `decimals`;
null = no clock, also what inputs recorded before 2.8.0 mean). In `now`
it is the clock a render would draw with the latest results fetched, so
the official time arriving or being corrected, the largada moved (0.5 ms
tolerance) or the clock turned on or off lists `clock` in `changed`.

`Job.clip_inputs` (`ClipInputsComparison`) puts the **newest** version's
inputs (`clip`) next to what a render would use now (`now`: the review as
saved, the Settings' name choices as they are) and lists in `changed`
every input that differs: `cut` (in and out together, 0.5 ms tolerance),
`prova`, `car_number`, `passagem`, `show_pass`, `team_name`,
`school_name` (the texts, so a name choice that draws the same text is no
difference), `raw`, `rotation`, `framing` (framing and the crop band's
place, half a percent tolerance), `audio`, `pass_result` and `clock`. The theme version is recorded, not compared (only
workers know theirs). `clip_inputs` is null without a clip version, for a
version without recorded inputs and for a discarded job;
`JobSummary.clip_outdated` is true when `changed` is not empty;
`ManifestEntry.clip_inputs` is the same comparison for the clips page;
`GET /review/queue?outdated=true` returns only outdated jobs (filtered
before `limit`). A reprocess (§7.1 "Review update") is the only fix:
saving never touches a clip that exists.

**Delivery decision.** Every `process` offer (and claim answer) carries
`delivery: {mode, studio_url}`. The orchestrator says `direct`, with
`studio_url = http://<studio tailnet address>:<receiver_port>/receive/<job_id>`,
when a studio heartbeated within the last 45 s and reported an address;
otherwise `outbox` with `studio_url: null`. A worker on a shared tailnet
(below) always gets `outbox`. Workers do not look at `GET /workers` to
decide. Before pushing, a worker checks `GET <studio>/health` with a 3 s
timeout and uses the outbox at once if it fails, remembering that studio
as unreachable for 5 minutes (LIVESTACK-41 item 13).

**Shared tailnet ("rede compartilhada", LIVESTACK-41 item 4).** A worker
whose heartbeats say `shared_tailnet: true` stays on its own tailnet, into
which the VPS is shared ("Usar a rede atual" in `--first-run`). It reaches
the orchestrator and Caddy, but peers and the studio cannot reach it, nor
it them. So it is never chosen as second holder, never listed as a peer
source (raws or clips), only gets VPS sources itself, and delivers through
the outbox. `Worker.shared_tailnet` shows it; the fleet page says "rede
compartilhada". The master is still always a holder (do not run the
master that way during an event).

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
lease is still being `renewed` 60 s after the push completed, look the job
up: delivered or done with this clip means the ACK arrived meanwhile and
you are finished; otherwise upload the clip to the outbox. (Without that
check, a late ACK made the studio receive the same clip twice, the second
time as an outbox pull onto the file playing on air: the live
`[WinError 5]` of 2026-11-05.) Once the studio's ACK is in, the job is
`delivered`/`done` and no `deliver` is offered for it.

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
| `PUT /jobs/{job_id}/media-info` | `ProbeReport` JSON, `Lease-Id` → 204 | make_proxy lease; the raw's ffprobe report (contract 2.1.0, below) |
| `POST /jobs/{job_id}/proxy` | chunk (+ `Frame-Rate`) → `ChunkAccepted` | ends make_proxy lease; sets `Job.frame_rate` |
| `POST /jobs/{job_id}/clip` | chunk → `ChunkAccepted` | outbox route; claimed → uploaded |
| `POST /jobs/{job_id}/delivered` | `DeliveredRequest` → `JobStateResponse` | studio; direct route |
| `POST /jobs/{job_id}/clip-archived` | `ClipArchivedRequest` → `JobStateResponse` | master, `archive_clip` lease; 422 `hash_mismatch` if the sha256 is no version of the job; state unchanged |
| `POST /jobs/{job_id}/original-upload` | chunk → `ChunkAccepted` | `upload_original` lease; Upload-SHA256 must be the job's (422); ends the lease; state unchanged |
| `PUT /jobs/{job_id}/render-inputs` | `RenderInputsReport` JSON, `Lease-Id` → 204 | process lease, before the upload or push; what the clip was rendered from (§5 "Render inputs"); state unchanged |
| `GET /jobs/{job_id}/original` | → `OriginalDownload` | where "Baixar original" stands; no side effects |
| `POST /jobs/{job_id}/original` | → `OriginalDownload` | "Baixar original"; see below |
| `GET /review/queue?state=…&limit=…&outdated=…` | → `ReviewQueueResponse` | default `awaiting_review`; in filming order, oldest `recorded_at` first, ties by arrival (below, "Recording time"); `outdated=true`: only jobs whose clip no longer matches the review (§5 "Render inputs"); each item carries `received_at` and `sender_message_count`, and (LIVESTACK-51) `sender_phone` / `sender_display_name` (the contact's key and name, for "Enviado por"), `reviewing` (who has the job page open) and `duplicate_of` |
| `GET /review/car-videos?prova=…&car_number=…` | → `CarVideosResponse` | one car's jobs in one prova, discarded left out, filming order; the job page's passagem panel (below) |
| `GET /jobs/{job_id}` | → `Job` | |
| `PUT /jobs/{job_id}/presence` | → `JobPresenceResponse` | reviewer session; "this job page is open", on opening and every ~20 s; answers the *other* reviewers on the job (`others`, first arrived first, `since` = their first ping). In memory; 45 s without a ping = gone. Never a lock (LIVESTACK-51); 404 unknown job |
| `DELETE /jobs/{job_id}/presence` | → 204 | the page closed; always 204 |
| `GET /jobs/{job_id}/sender-messages` | → `SenderMessagesResponse` | reviewer session only; the job page's "Mensagens de <remetente>" (below, "Sender messages"); 404 unknown job |
| `GET /jobs/{job_id}/pass-preview?prova=…&car_number=…&passagem=…` | → `PassPreview` | reviewer session only; the result line and the clock for values not saved yet (below, "Running clock"); nothing is stored; 404 unknown job |
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
| `GET/POST /admin/contacts`, `PATCH /admin/contacts/{phone}` | `CreateContactRequest`/`UpdateContactRequest` → `Contact` (`ContactCreated` on POST) | see "Contacts" below; null clears name, default prova, notes |
| `POST /admin/contacts/{phone}/welcome` | → `WelcomeSendResponse` | sends the welcome message (again); see "Welcome message" below |
| `POST /admin/provas`, `PATCH /admin/provas/{prova_id}`, `PUT /admin/provas/order` | → `Prova` / `ProvasResponse` | manual provas; synced ones only (de)activate |
| `GET /admin/teams`, `PUT/DELETE /admin/teams/{car_number}` | `SaveTeamRequest` → `TeamRow` | on a synced team PUT stores a correction kept across syncs, DELETE removes it |
| `POST /admin/results-sync` | → `ResultsSyncReport` | see "Results API" below; 422 unknown event, 503 API down/limited |
| `GET /admin/enrollment-tokens`, `POST …/{token_id}/revoke` | → `EnrollmentTokensResponse` / `EnrollmentTokenInfo` | unused/used/expired/revoked; 409 for a used one |
| `PATCH /admin/workers/{worker_id}` | `UpdateWorkerRequest` → `Worker` | the display name (below); revoked workers too |
| `POST /admin/workers/{worker_id}/make-master` | → `Worker` | "Tornar master" (below); 409 for a studio or a revoked worker |
| `POST /admin/workers/{worker_id}/revoke` | → `Worker` | its token answers 401, leases end, holder rows dropped |
| `GET/PATCH /admin/settings` | `UpdateSettingsRequest` → `SettingsResponse` | `.env` values are first-start defaults only; secrets only as configured/missing; the clip name choices, the welcome message, the vertical-video reply and `sender_messages_keep_days` have no `.env` value |
| `GET /admin/pass-results` | → `PassResultsSettings` | the configuration as saved and each configured prova's fetch status; see "Pass results" below |
| `PUT /admin/pass-results/config` | `UpdatePassResultsConfigRequest` → `PassResultsSettings` | checked first: 422 `validation_error` with `details.path` and `details.problem`, the one in force stays; audit kind `pass_results_config_updated` |
| `POST /admin/pass-results/test` | `PassResultTestRequest` → `PassResultTestResponse` | "Testar": fetches the table now (reuses one from the last 5 s) and builds the line; `config_text` tries an unsaved configuration |
| `GET /admin/audit?area=&limit=` | → `EventsResponse` | newest first |

**Worker names** (LIVESTACK-42). `Worker.name` is the **display name**:
what the volunteer typed on the token form (prefilled with the host name,
sent as `EnrollRequest.worker_name`), changed later by an admin with
`PATCH /admin/workers/{id}` (audit kind `worker_renamed`). Every page,
event line and the fleet show it. `Worker.hostname` is the PC's host name
as reported at enrollment (`EnrollRequest.hostname`), shown as secondary
text; nothing changes it.

**One master, switched from the UI** (LIVESTACK-42). `POST
/admin/workers/{id}/make-master` makes that worker the master and every
other non-revoked master a `worker`, in one transaction (audit kind
`master_changed`); the demoted master's `archive_clip` leases end at once.
Promoting the master again changes nothing. Only master ↔ worker: a studio
has another tailnet tag and must enroll again (409 `conflict`), and so is a
revoked worker. The PCs learn it from the heartbeat (§5 "Role").

**Contacts.** Every sender of a private message is recorded (status
`unknown`, display name from `pushName`). For a video: `blocked` → 200
ignored, no reply; in intake mode `allowlist`, not `allowed` → 200 ignored
and `SENDER_NOT_AUTHORIZED` at most once per 24 h; otherwise the job is
created with the contact's default prova filled in.

**One contact per phone** (LIVESTACK-51, `domain/phones.py`). A
Brazilian mobile is kept as 55 + DDD + 9 + 8 digits, whether the JID or
the admin wrote it with or without the 9th digit (a 12-digit 55 number
whose 8-digit part starts with 6–9 gets the 9; landlines are left alone),
and a number typed as DDD + 8 or 9 digits gets 55. `Contact.phone` is that
form; a `{phone}` in the path may be written any way. Migration 0017 merged
existing twins (admin event `contact_merged`). `Contact.waiting_without_prova`
counts the sender's videos still waiting for review (received, proxy_ready,
awaiting_review) with no prova; `PATCH /admin/contacts/{phone}` with
`apply_default_prova_to_waiting: true` fills the contact's default prova
(after the change) into them, logging `default_prova_applied` on each job,
without bumping their version. A prova already chosen is never replaced.

**Welcome message** (LIVESTACK-44). `AdminSettings.welcome_message` (pt-BR
default shipped, edited on Settings; empty turns it off) is sent with
Evolution `sendText` to a contact's number, with `{nome}` (display name)
and `{prova}` (default prova) filled in; with no value the placeholder,
the space and a comma right before it disappear ("Olá, {nome}!" →
"Olá!"), any other `{…}` stays as typed
(`shared/welcome_message.py`). `POST /admin/contacts` with `send_welcome:
true` sends it after storing the contact and answers `ContactCreated`, a
`Contact` plus `welcome: {sent, error}` (null when not asked); the contact
is created whatever happens to the message. `POST
/admin/contacts/{phone}/welcome` sends it again: 200 with `{contact,
welcome}` even when Evolution refused (`sent: false`, its reason in
`error`), 409 `conflict` for a blocked contact or an empty message, 404
for an unknown contact. On create a blocked contact or an empty message is
not tried (`sent: false`, no event). Each try is an admin event,
`welcome_sent` or `welcome_failed` (`details.after`: phone, text, error);
`Contact.welcome_sent_at` is the last accepted send.
`ContactsResponse.welcome_message_set` says whether a message is set.

**Results API.** Provas and teams come from the Baja results API
(`GET {base}/resultados/evento.php?evt=` and `equipes.php?evt=`; contract:
`docs/results-api.md` in `baja-sae-brasil-online`) for the event id set in
Settings: on change, `POST /admin/results-sync`, and every 6 h (15 min
after a failure). Provas are keyed by the API's prova id, teams by car
number. A sync adds and renames (a renamed prova relabels its jobs and keeps
its slug), and marks rows the API no longer lists inactive; it never
deletes. A manual prova with the same clip folder as a new synced one is
adopted. `Job.team_name` keeps the team's full name. The team's short
name, the school's full name and its sigla (the API's `school_short`, or an
admin's correction on the Teams page) are copied onto the job when the car
number is set.

**Pass results** (LIVESTACK-47, contract 2.7.0; design §32). The
official result of a passagem, burned into the clip as a third pill
("66 s · 4 cones → 86 s"). Configured on Settings as one JSON document
(`GET/PUT /admin/pass-results…`), keyed by the API's prova id (synced
provas carry it as `external_id`); the event is the Settings' event id,
so `"MAN"` reads table `26BR_MAN`. The orchestrator fetches
`resultado.php?evt=…&id=…&display=1` for configured provas with jobs in
`awaiting_review`, `ready`, `claimed`, `uploaded`, `delivered` or `done`:
every 30 s, or the answer's `Cache-Control: max-age` when longer, until
the event is finished (max-age 300 s); then one last fetch and no more
(`PassResultFetch.event_finished`). A failure keeps the last good answer
(`PassResultFetch.stale`, `failing_since`). One function
(`orchestrator/domain/pass_text.py`) builds the line for the render task
(`ProcessTask.pass_result_text`, only to workers that declared
`pass_result`), the job page (`Job.pass_result`: `configured`, `show`,
`text` or `problem`, `fetch`), "Testar" and the render-input comparison.
`Job.show_pass_result` (also `ReviewUpdate`) is the reviewer's toggle;
null follows the prova's `show_by_default`. A prova's `result.extra`
columns are appended as `· <value> <unit>`. Values `"-"`, `"---"`, empty,
`"SPOILERS"`, `"?"` and any text that is not a number never reach the
overlay (`problem`: `no_result_yet`, `not_a_number`). An `extra` item's
`unit` may also be names by count, `{"one": "obstáculo", "other":
"obstáculos"}` ("one" when the number as shown is exactly 1; LIVESTACK-48).

**Running clock** (LIVESTACK-48, contract 2.8.0; design §33). For a
prova whose configuration has a `clock` block, the clip shows a clock that
starts at 0 on the **largada** and freezes on the **official time** (the
`clock.column` value of the run, the raw time, not the corrected one).
`Job.largada_s` is the reviewer's mark in raw-video seconds
(`ReviewUpdate.largada_s`; left out = keep, `clear_largada: true`
removes it; 422 past the end of the video); `Job.show_clock` the toggle
("Mostrar cronômetro"; null follows `show_by_default`). `Job.pass_clock`
(`PassClock`): `configured`, `show`, `largada_s`, `decimals`,
`official_units` and `official_text` ("66,0") or `problem`, `fetch`, and
`render`, the `RenderClock` a render would draw now (null without a
largada, an official time, or with the toggle off).
`ProcessTask.pass_clock` carries it, only to workers that declared
`clock`. What the clock shows at raw time *t*: `min(⌊(µs(t) − µs(largada))
· 10^decimals / 10⁶⌋, official_units)` units, each time rounded half up
to whole microseconds, nothing before the largada; written with a decimal
comma. `Job.clip_team_name` and `clip_school_name` are the names a render
would draw now (for the job page's overlay preview).
`PassResultTestResponse` ("Testar") adds `clock_configured`, `clock_text`
and `clock_problem`.

**Before a save** (contract 2.10.0, LIVESTACK-53). `GET
/jobs/{id}/pass-preview?prova=…&car_number=…&passagem=…` (reviewer
session) answers `PassPreview {pass_result, pass_clock}`: exactly what
`Job.pass_result` and `Job.pass_clock` would be after saving those values
(the same functions, run on the saved job with the asked values put in).
Nothing is stored. A value left out is the saved one, as in `PATCH
…/review`; the toggles and the largada are always the saved ones (the
page reads its own fields for those). Only tables already fetched are
used: the first video of a prova sees `not_fetched` until the background
loop fetches the table, which it does once a job of that prova is saved.
The job page asks while prova, car or passagem change, so "Resultado da
passagem" and "Cronômetro" appear, change and go without a reload.

**Names on the clips** (LIVESTACK-42). Two independent Settings,
`clip_team_name_style` and `clip_school_name_style` (`full` | `short`,
default `short`, i.e. the team's short name and the school's sigla), decide
which names a clip shows; a missing name falls back to the other one.
`orchestrator/domain/clip_names.py` applies them, when the task is offered,
to `ProcessTask.team_name` (the overlay's name pill) and
`ProcessTask.school_short` (the role pill: the sigla **or the full school
name**, despite the field's name), and to `ManifestEntry.clip_team_name` /
`clip_school_name` and `Team.clip_team_name` / `clip_school_name` (`GET
/teams/{car}`), which the review UI shows. `latest.m3u` keeps the full team
name.

**Evolution webhook.** The route is exactly **`POST
/intake/evolution-webhook`** with the header **`X-Webhook-Secret:
<BVP_WEBHOOK_SECRET>`** (wrong or missing → 401); Evolution posts it over
the VPS's internal Docker network (no public route), and `infra/vps/up.sh`
configures it as the Baja Bot **instance** webhook (no global webhook; nothing else consumes it).
Only `event == "messages.upsert"` from others (`fromMe == false`) in a
private chat is considered, plus `messages.edited` and `messages.delete`
(below, "Sender messages"): a `remoteJid` ending in `@g.us` or
`@broadcast`, or a key with a `participant` (group sender), → 200
`ignored: true`, no job and no bot reply (design §3, videos by DM only). `shared.models.find_incoming_video` decides: a
`documentMessage` (also inside `documentWithCaptionMessage`) with a
`video/*` mimetype → normal job, its caption kept as `IntakeSource.caption`;
a `videoMessage` → `low_res: true` and the bot asks for a resend as
document; anything else → 200 `ignored: true`, and kept for the job page
when the sender's videos would be accepted (`message_stored: true`).
The fields read are `url`, `directPath`, `mediaKey`, `fileEncSha256`,
`fileSha256`, `fileLength`, `mimetype`, `fileName`; binary ones arrive as
base64 text (Node's `{"type":"Buffer","data":[…]}` and index-keyed objects
are accepted too). `mediaUrl` is not used. The answer comes fast: 202
`accepted: true, job_id: null`; the media is then downloaded and decrypted
in the background (§6.3), and the job is created in `received` only after
every check passed. If that fails, no job exists and the bot asks the
sender to resend. A repeated `data.key.id` (in progress or done) → 200
`duplicate: true` with the existing `job_id` if any. The same *video* in a
new message (resent, forwarded by someone else) becomes a job discarded at
once as a duplicate: its raw SHA-256 (= `fileSha256`, already verified by
the download) matches a job that is not discarded. `Job.duplicate_of`
names the first job, `Job.duplicates` lists the copies on it; restore works
as for any discard (back to `received`). A re-encoded copy is not detected. Inbox above
`BVP_INBOX_CAP_GB` → 413 `inbox_full`; above `BVP_INBOX_HOLD_GB` the media
is accepted but the bot asks the sender to hold further videos.

**Bot answers** (`orchestrator/domain/bot_replies.py`) are attached to the
video they are about, because volunteers send several files in a row:

| Situation | Reaction on the video | Text (quoted reply to the video) |
| --- | --- | --- |
| verified, job created | ✅ | none |
| verified, but the same video as a job not discarded (duplicate, discarded at once) | ⚠️ | `AdminSettings.duplicate_video_message`, `{hora}` = when the first copy arrived (none when empty) |
| arrived as a WhatsApp video (`low_res`) | ⚠️ | `LOW_RES_RESEND_AS_DOCUMENT` |
| inbox above the soft cap | ⚠️ | `INBOX_NEARLY_FULL_PLEASE_HOLD` |
| inbox above the hard cap (413) | ❌ | `INBOX_FULL_RESEND_LATER` |
| download or a hash check failed | ❌ | `DOWNLOAD_FAILED_PLEASE_RESEND` |
| the job's first probe report shows a vertical picture (LIVESTACK-50) | none (the ✅ stays) | `AdminSettings.vertical_video_message`, once per video; empty = not sent |

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

**Originals for reviewers** (contract 2.3.0, LIVESTACK-41 item 15).
`POST /jobs/{id}/original` records the request (time of the last one).
While the inbox still has the raw it is hard-linked into
`originals/<job_id>.<ext>` and the answer is `ready` at once; otherwise
an online confirmed holder (the master first) is offered `upload_original`
and the answer is `preparing`, or `no_holder_online` when none is. `ready`
carries `download_url` = `/original-downloads/<job_id>/<raw_file_name>`
(relative to the public base URL; Caddy serves `originals/<job_id>.<ext>`
with the same `forward_auth` as proxies, saved under that readable name),
`file_name`, `size_bytes` and `expires_at`. Originals stay
`originals_keep_hours` (Settings, 24) after the last request and are
evicted earlier, oldest request first, when `originals/` passes
`originals_cap_gb` (Settings, 10) or free disk drops below
`proxy_evict_min_free_gb`. They are never holders and never a source for
processing.

**Media info** (contract 2.1.0). The first holder already runs ffprobe
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

**Picture and render options** (contract 2.6.0, LIVESTACK-50). The
media-info report also gives `Job.display_width/height`: the first video
stream's size after its rotation note (`side_data_list[].rotation`,
counterclockwise in ffprobe, else the `rotate` tag), e.g. 1080×1920 for a
phone filmed upright that stores 1920×1080 frames. `Job.vertical` /
`JobSummary.vertical` say whether it is taller than wide after the
reviewer's `rotation`. The review fields gain `rotation` (0, 90, 180, 270,
clockwise), `framing` (`blurred` default, `black_bars`, `crop`),
`crop_position` (0…1, default 0.5) and `no_audio` (default false), saved
by `PATCH …/review` like the others (left out = keep) and copied into the
process task. Every clip is 1920×1080: a picture that is 16:9 after the
turn renders exactly as before; any other is fitted and filled as
`framing` says (design §29). The job's first report of a vertical video
also makes the bot quote it once (`AdminSettings.vertical_video_message`,
"Bot answers" above), sent after the worker's PUT is answered; event
`vertical_reply_sent` or `vertical_reply_failed`.

**Recording time** (contract 2.5.0, LIVESTACK-45). `Job.recorded_at` /
`JobSummary.recorded_at` is when the video was filmed, and
`recorded_at_source` where that came from:

| Source | Value | When |
| --- | --- | --- |
| `metadata` | the raw's `creation_time`: format tags, else the first video stream's tags (from the media-info report above) | plausible: not after `source.received_at`, not more than 7 days before it |
| `whatsapp` | `source.sent_at`, the webhook's `messageTimestamp` | no plausible metadata; the same plausibility test |
| `received` | `source.received_at`, when the webhook arrived | neither of the above |

A job starts with `whatsapp` or `received` at intake and moves to
`metadata` when the media-info report arrives (no review version bump).
Only `metadata` is exact; the review UI marks the others as hints. A
`creation_time` without a zone is read as UTC. Jobs from before 2.5.0 got
their creation time (`received`), then, once at the first start, the
`creation_time` of the probe report already stored. No worker change:
the report already carried the tags.

**Car videos** (contract 2.5.0). `GET /review/car-videos?prova=…&car_number=…`
(reviewer session) lists every job of that car in that prova as
`JobSummary` items, discarded ones left out, oldest `recorded_at` first
(ties by arrival), and says the prova's folder in `prova_slug`. `prova`
may be a name or a slug: jobs are matched by the prova's clip folder, not
by name, so a renamed prova (or a page that still shows the old name)
finds the same jobs; `prova_slug` is null for a prova with no letters or
digits (no jobs). The asking job is in the list when it is saved with that
prova and car. The review UI places the video among them and works out
the passagem suggestion and the warnings itself
(`review_ui/passagem_help_view.py`); the orchestrator only lists.

**Sender messages** (contract 2.6.0, LIVESTACK-49). A private message
that is not a video, from a contact whose videos would be accepted (not
blocked; in allowlist mode, allowed), is kept for the job page, never
answered: text (`conversation`, `extendedTextMessage`), captions of images
and documents, and for anything else only its kind (`audio`, `image`,
`sticker`, `location`, `contact`, `document`, `other`). Nothing is
downloaded. Reactions and poll votes are not kept. Stored: the WhatsApp
id, the sender, when it was received (the webhook's arrival) and the
quoted message's id (Evolution 2.3.7: `data.contextInfo.stanzaId`, since
it rewrites a reply into `conversation`; `<content>.contextInfo.stanzaId`
is read too). Edits and deletions arrive as `messages.edited` with `data`
= WhatsApp's protocolMessage (`key.id`, `type` 14 / `"MESSAGE_EDIT"` with
`editedMessage`, or 0 / `"REVOKE"`) and, for deletions, `messages.delete`
(`data` = the key plus `status: "DELETED"`); both answer 200,
`message_stored: true` when a kept message changed. An edit replaces the
text (`edited: true`); a deletion drops it (`deleted: true`, no text).
Messages older than `AdminSettings.sender_messages_keep_days` (14) are
deleted by the background loop; the video's own caption stays with its job.

`GET /jobs/{id}/sender-messages` (reviewer session; workers never get
these) answers `SenderMessagesResponse {job_id, sender_name, items}`, each
item `{message_id, role, kind, text, received_at, edited, deleted}`, in
display order: `caption` (the video's), `reply` (quotes this video,
whenever sent), `after` (received after this video and before the
sender's next one), `before` (after the sender's previous video and before
this one; for the first video, everything earlier still kept), oldest
first within each role. Every job of the sender is a boundary, whatever
its state; a reply quoting another of the sender's videos is listed only
there; a message at the same instant as a video counts as after it.
`JobSummary.sender_message_count` (in `GET /review/queue` and `GET
/review/car-videos`; 0 elsewhere) is the number of items, and
`JobSummary.received_at` the job's `source.received_at`.

**Review update.** `expected_version` must equal the job's `version`
(else 409 `version_conflict`). Since `version` only moves on review
actions (§4), the conflict means another reviewer changed the job.

| `action` | From states | Effect |
| --- | --- | --- |
| `save` | any except `discarded` | store fields/cuts only |
| `ready` | `awaiting_review` | → `ready`; needs all fields |
| `reprocess` | `claimed`, `uploaded`, `delivered`, `done` | stores the fields sent with it and → `ready` in one request (the review UI's "Salvar e reprocessar"); revokes any process lease; needs all fields; accepted with no holder online (`waiting_reason` then says so at once). The next rendering is the next clip version, delivered under its `-vN` name (§8.1) |
| `discard` | `awaiting_review`, `ready`, `failed` | → `discarded`, records `discarded_from`; nothing deleted during the grace period (§4); fields not needed |
| `restore` | `discarded` | → `discarded_from`; 409 `raw_unavailable` after the grace period when no copy of the raw is left; fields not needed |
| `retry` | `failed` | → `failed_from` (`claimed` → `ready`); clears `failure`; needs all fields only when the target is `ready` (may be corrected in the same call) |

Other combinations → 409 `illegal_transition`. "All fields" = `prova`,
`car_number`, `passagem` set after the update, else 422
`missing_review_fields`. Cuts are optional: on `ready` and `reprocess` a
missing `cut_in_s` is stored as 0 and a missing `cut_out_s` as the probed
`duration_s` (the whole video); only when no duration is known yet is a
missing `cut_out_s` still reported in `missing`. `save` never fills them.
`team_name` is resolved from `car_number`. Cuts must satisfy
`0 ≤ in < out ≤ duration`.

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
background until it succeeds; repeating it is harmless). Moving into
place (LIVESTACK-41 item 13): if the folder already holds a clip with the
same SHA-256 (under the path or a `-vN` name), the push is complete at
once and nothing is renamed; a rename that hits a Windows lock is retried
for a few seconds; a file that stays locked (on air) is never
overwritten: the new clip is stored as `…-v2.mp4` (next free number), and
that path is the one in `latest.m3u` and in the ACK's `relative_path`. A
second push of a clip already ACKed sends no second ACK.

### 7.3 Localhost-only endpoints (127.0.0.1:8700)

Served by the worker for the volunteer's own PC: the setup/status page and
a small JSON API that the tray icon (`BajaWorkerTray.exe`) uses. Never on
the tailnet, no login. Requests whose `Host` is not `localhost`/`127.0.0.1`
→ 403; POSTs also need `Content-Type: application/json` and no foreign
`Origin` (so other web sites cannot press the buttons). Details:
`worker/README.md` ("Status page, pause and the tray icon").

| Method & path | Behaviour |
| --- | --- |
| `GET /` | always answers, even with a broken `worker.json`: the problem (file, line, column, hint), the token form before enrollment, a studio's clip-folder form, then the pt-BR status page. It never reloads itself |
| `POST /setup` | the token form (before enrollment only) |
| `POST /setup/studio-folder` | a studio's clip folder (only while it has none) |
| `GET /api/status` | status document (`phase`, `enrolled`, `connection_step_pending`, `config_problem`, `tailnet_problem`, `identity_rejected`, `can_re_register`, `paused`, `last_contact_at`, `unreachable_for_s`, `tasks`, `storage`, `held_raws`, `clips_held`, `clips_archived`, `studio`, `details`); never the bearer token |
| `GET /api/page` | the page's live parts as HTML, for the in-place refresh |
| `POST /api/pause` | "Pausar": finish the current task, take nothing new, heartbeat `paused: true` (§5); stored in `worker.json` |
| `POST /api/resume` | "Retomar" |
| `POST /api/stop` | "Desligar o worker": stops the Windows service (`sc stop BajaWorker`) |
| `POST /api/re-register` | "Registrar de novo": clears only the identity in `worker.json` (worker id, token, role, tailnet URL and address, `shared_tailnet`), keeps every other setting and the videos, and goes back to the token form |

## 8. Folder layouts

### 8.1 Studio box (design §7)

```
<clip root>/                       chosen at studio setup (default C:\BajaClips)
├── latest.m3u                     newest first, max 50, relative paths
└── <prova-slug>/<car>/<prova-slug>-<car, 3 digits>-p<passagem>[-vN].mp4
    e.g. enduro/42/enduro-042-p3.mp4, suspensao/7/suspensao-007-p1.mp4
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
sees half a file. File names say what the clip is (LIVESTACK-41 item 12,
`shared.layout.clip_file_stem`, one line to change the format); clips
stored before keep their `<passagem>.mp4` names. A reprocessed clip is
rendered and delivered **under its version name** (LIVESTACK-43): the
`process` task's `clip_relative_path` (and the outbox `deliver` task's)
is `enduro/42/enduro-042-p3-v2.mp4` for version 2, so the clip on air is
never written over; the studio puts the new version at the top of
`latest.m3u` and deletes the older version once nothing holds it open. (A
file that is locked anyway still gets the next free `-vN` beside it,
§7.2.) Clip versions keep the first name (`relative_path`), from which
archive and download names are built; `Job.clip.relative_path` and
`ManifestEntry.relative_path` are the studio's actual path. The master's archive keeps every version
(`enduro-042-p3-v2.mp4`, `shared.layout.archive_relative_path`), and
`ManifestEntry.version` says which version is current. Downloads of a
clip are named the same way: `/clip-downloads/<job_id>/enduro-042-p3.mp4`
(`ClipInfo.download_file_name`).

### 8.2 VPS (`BVP_DATA_DIR`, default `/srv/pipeline`)

```
inbox/<job_id>.<ext>      raws until 2 holders confirm; Caddy :8080 /inbox/*
proxies/<job_id>.mp4      Caddy :443 /proxies/<job_id>.mp4 (forward_auth → /auth/media)
thumbs/<job_id>.jpg       Caddy :443 /thumbs/<job_id>.jpg (forward_auth → /auth/media)
originals/<job_id>.<ext>  raws a reviewer asked for (item 15), temporary; Caddy :443
                          /original-downloads/<job_id>/<readable name> (forward_auth → /auth/media)
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
raws/<raw_file_name>      held raws, e.g. joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4
                          (DownloadRawTask.raw_file_name: sender, when the bot received it
                          in São Paulo time, job id start; older jobs: <job_id>.<ext>);
                          served on :8710, never evicted, deleted on discard
raws/.raw-names.json      readable raw name → full job id
clips/<job_id>.mp4        the clip being rendered/delivered (+ .json sidecar)
clips/<clip name>.<job_id>.<sha256>.mp4   delivered clips (e.g. enduro-042-p3.…), kept until
                          archived by the master (served on :8710 /clip; evicted near the cap
                          only once archived)
clips/<prova>/<car>/<prova>-<car>-p<passagem>[-vN].mp4   master only: the archive of every version
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
