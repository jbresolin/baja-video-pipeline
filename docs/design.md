# Baja video pipeline v2 — design

Source of truth for this repo. Consolidated from the Plane page
"01 — Video pipeline v2 — design" (LIVESTACK, 2026-10-25) plus the
implementation decisions in §13. Plane work items: **LIVESTACK-6**
(deliverable) → -24 orchestrator, -25 worker, -26 fleet view,
-27 review UI, -28 delivery, -29 tailnet, -30 Evolution migration.

## 1. Why

The current pipeline (Drive → Google Sheet/Apps Script → TrueNAS Cloud
Sync → processor on TrueNAS → Drive → streaming PC) depends on residential
internet at home and on a single GPU during a live event. v2 moves intake
to the Baja staging VPS, parallelizes editing across fast PCs, and replaces
the whole Drive/Sheet/GAS/Cloud Sync layer with one jobs DB.

## 2. Hosts and roles

| Host | Runs | Role |
| --- | --- | --- |
| Baja staging VPS (2 vCPU, 4 GB, 70 GB) | Evolution API (Baja Bot account), orchestrator, review UI, Caddy | Receives files, coordinates workers, serves proxies. **Transit only** for raws. |
| Master worker = João's desktop (RTX 5080) | worker app | Always a holder of every raw; single backup source after the event |
| Other workers (volunteers' Windows PCs) | worker app (single installer) | Hold raws, make proxies, edit, deliver |
| Streaming PC (event) | worker app in `studio` role (receiver), OBS | Receives finished clips and plays them |
| TrueNAS (home) | — | Out of the event path; post-event raw archive copied from the master |

All of the above except TrueNAS are on a **Baja tailnet** (§8).

## 3. Intake

* Volunteers send each video **as a WhatsApp document** (not as a video,
  which WhatsApp recompresses) by DM to the **Baja Bot** number. No group.
  Document limit ~2 GB.
* The Baja Bot Evolution instance migrates from TrueNAS to the VPS
  (LIVESTACK-30), Manager UI reachable behind Caddy auth or the tailnet.
  BajaBot/n8n is dormant (all workflows disabled) and is not a consumer of
  this instance; this pipeline is the only webhook consumer.
* Evolution webhook → orchestrator saves the file to the inbox, computes
  SHA-256, creates the job.
* A file that arrives as a recompressed *video* (not a document) is
  **accepted and flagged `low_res`**; the bot auto-replies asking for a
  resend as document. The reviewer sees the flag. (Decision, §13.)

## 4. Job state machine

```
received → proxy_ready → awaiting_review → ready → claimed (lease) → uploaded → delivered (box ACK) → done
                              │               │        │
                              └── discarded ◄─┘        └── failed ──► ready (retry) | discarded
```

* `discarded`: a reviewer drops a junk or accidental video (from
  `awaiting_review` or `ready`); held raws may be deleted by workers.
* `failed`: a worker reports an unrecoverable error (corrupt file, ffmpeg
  exit) from `received`, `proxy_ready` or `claimed`; the job records
  `failed_from`, the reviewer sees the message and chooses retry (back to
  `failed_from`) or discard.

* **Holders.** Two workers download each raw and verify the hash. The
  **master is always one holder** (downloads everything, even what it never
  edits). The second is chosen by the orchestrator among the fastest
  available downloaders. When both hold it, the VPS deletes its copy.
* **Master offline:** two other workers hold; the master backfills later
  from a peer over the tailnet. Workers serve held raws to each other.
* **Proxy.** The first holder renders a 480p ~1.5 Mbps proxy (NVENC if
  available, `-movflags +faststart`, constant frame rate so proxy time maps
  1:1 to the raw) and uploads it. Proxies are worker-made only (VPS never
  transcodes).
* **Review** (§6) sets cut in/out and fields → `ready`.
* **Processing.** A holder claims the job under a lease (expires on missed
  heartbeats), runs the ffmpeg pipeline (trim, overlay, loudnorm), uploads
  the clip.
* **Reprocessing** (reviewer changed a cut) is routed only to holders; if
  both are offline, any worker may fetch the raw from a holder when one
  returns; until then the job waits and the UI says so.

## 5. Scheduling and balancing

* **Pull model:** each worker holds at most one job per stage and asks for
  the next when free; 15 s heartbeats.
* No calibration phase. The orchestrator keeps rolling per-worker stats
  (download Mbps, process s/GB, upload Mbps) from the first jobs; used for
  second-holder placement and the fleet view.
* Worker drops → its leases expire and re-queue. New worker → eligible as
  soon as it registers.

## 6. Review

* Web UI on the VPS: incoming queue, thumbnail, proxy player, keyboard
  in/out marks, fields: prova, car number, passagem, show-pass flag. Team
  name resolved from the Baja app by car number.
* Proxies served **statically by Caddy** with HTTP range requests (no app
  process in the byte path); ~8 Mbps for five concurrent reviewers.
* **Retention:** proxies stay on the VPS. Evict only above a disk
  threshold, oldest completed jobs first. Any holder regenerates a proxy on
  request.

## 7. Delivery

* Worker pushes the finished clip **directly to the streaming PC** over the
  tailnet (receiver on the box = worker app in `studio` role).
* Fallback: VPS outbox; the streaming PC pulls, verifies checksum, ACKs;
  VPS deletes after ACK.
* A worker on the event LAN writes straight to the box's share.
* On the box, clips land in a folder layout OBS can play directly
  (`<prova>/<car>/<passagem>.mp4` plus a `latest.m3u` playlist), and the
  orchestrator exposes a **clip manifest endpoint**. A playout UI is a
  separate, later deliverable (LIVESTACK-8) reading that manifest.

## 8. Tailnet and enrollment

* Tags `tag:server` (VPS), `tag:worker`, `tag:studio` (streaming PC) with
  ACLs; orchestrator API and box receiver never public.
* One generic installer. The orchestrator issues a **one-time enrollment
  token** per volunteer; on first run the worker exchanges it for a
  single-use, tagged, pre-approved, **non-ephemeral** Tailscale auth key
  minted through the Tailscale API (OAuth client, auth-keys scope). Tagged
  nodes have no key expiry. A leaked installer grants nothing; every join
  is attributable.
* The same exchange also returns a **per-worker bearer token** used on
  every orchestrator call (§13).
* **Tags:** `tag:server`, `tag:worker`, `tag:studio`, plus `tag:enroller`,
  which is never applied to a device: it is the tag of the orchestrator's
  OAuth client and owns `tag:worker` and `tag:studio`, so one client can
  mint keys for either role (Tailscale requires a client-minted key to
  carry the client's tags or tags they own).
* **Exception to "never public":** `POST /enroll` must be reachable before
  the new PC is on the tailnet, so Caddy exposes that one route publicly,
  rate-limited; the token is one-time and high-entropy. Everything else is
  tailnet-only.
* **Ports (contract):** 443 Caddy public side (review UI, proxies, thumbs,
  `/enroll`, Evolution's own API/Manager UI; the Evolution→orchestrator
  webhook stays inside the compose network); 8080 Caddy tailnet side, published on the
  Tailscale IP only, plain HTTP (worker/studio API, inbox/outbox files);
  8000 orchestrator, reachable only inside the compose network; 8710 worker
  file server; 8720 studio receiver. Full table and ACL in `infra/tailnet/`.
* **Minting spec:** `infra/tailnet/mint_auth_key.md` (unused key expires
  after 1 h, fallback 86400 s if rejected). Env vars
  `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_CLIENT_SECRET`,
  `TAILSCALE_TAILNET`, `TAILSCALE_API_BASE` (tests point it at a fake).

## 9. Worker packaging

* Python app frozen with **PyInstaller (onedir)**, wrapped in an **Inno
  Setup** installer: one UAC prompt, bundled BtbN NVENC ffmpeg, silent
  Tailscale MSI, NSSM service with auto-start, uninstall entry.
* First-run setup page on localhost: enrollment token, **working folder**,
  **storage cap**. Nothing else.
* Near the cap the worker stops volunteering as a holder but keeps
  processing what it holds (held raws are never evicted).
* Unsigned build → SmartScreen "unknown publisher"; document the
  click-through. NVIDIA GPU recommended, not required: the worker probes
  for `h264_nvenc` and falls back to `libx264 -preset fast`.

## 10. VPS sizing and caps

* Estimate: 200 passes × 500 MB = 100 GB raw/day through the VPS, pulled
  twice = 200 GB egress ≈ 4.5 h at 100 Mbps spread over the day.
* Inbox holds only files waiting for a second holder → normally
  near-empty. **Hard cap ~25 GB**; near it, the bot tells senders to hold.
  Only fills if all workers are offline.
* Proxies ≈ 6 GB per 200-pass event. Outbox used only on fallback, deleted
  on ACK.
* Evolution + Postgres + Redis are the real RAM consumers on the 4 GB box.

## 11. After the event

* ~~Raw archive = copy the master worker's folder to TrueNAS, unhurried.~~
  Superseded 2026-11-04 (LIVESTACK-38, decision by João): **processed
  clips are the archive; raws are deleted by hand after the event.**
* Workers keep every clip they render (`clips/<job>.<sha256>.mp4`) and
  evict one only after the master archived that exact clip, near the cap,
  oldest first. The master keeps a copy of **every** finished clip
  (task `archive_clip`, lowest priority), every version of a reprocessed
  pass (`<passagem>.mp4`, `<passagem>.v2.mp4`, …), in
  `clips/<prova-slug>/<car>/` like the box. Outbox copies go only after
  the studio ACK and the archive (or 72 h after the ACK).
* Post-event archive = copy the master's `clips/` folder to TrueNAS
  (`infra/vps/EVENT_DAY.md` "After the event"), then delete the raws.

## 12. Open questions (non-blocking)

1. How the number + "send as document" instruction reaches volunteers.
2. Which volunteers lend PCs, and their GPUs.

## 13. Implementation decisions (2026-10-25)

* **Monorepo** `jbresolin/baja-video-pipeline`:
  `orchestrator/`, `review_ui/`, `worker/`, `shared/` (schemas, API
  client, hashing, ffmpeg helpers), `infra/` (compose, Caddy, Tailscale
  ACLs, runbooks), `docs/`. The review UI is its own package and service
  (`review_ui/`, served under `/review` by Caddy, §16), talking to the
  orchestrator over its HTTP API. The old `baja-video-processor` repo is
  reference only; nothing is ported verbatim.
* **One worker codebase, three roles:** `master`, `worker`, `studio`.
  `studio` never volunteers as a holder and exposes the receive endpoint.
* **Stack:** Python 3.12, FastAPI, SQLite (WAL mode), `httpx`, `pydantic`,
  pytest, `uv` for dependencies, ruff for lint/format. Review UI is
  server-rendered HTML (Jinja2) + small vanilla JS modules, no build step.
* **Transfers:** plain HTTP over the tailnet with `Range` + SHA-256 resume.
  VPS→worker via Caddy static files; worker↔worker and worker→studio via a
  small HTTP file server inside the worker.
* **Auth:** per-worker bearer token issued at enrollment, checked by the
  orchestrator. The tailnet is the network boundary; the token is the
  identity. Everything is testable locally without Tailscale.
* **Testing:** pytest everywhere. Fakes for Evolution (a webhook poster),
  ffmpeg (tiny generated clips, libx264), Tailscale (token exchange
  mocked). Nothing in the test suite needs a GPU, a network or a WhatsApp
  account.
* **Workflow:** one branch per work item, PR to `main`, João reviews and
  merges. Plane statuses track PRs.

## 14. Code standards (apply everywhere)

The code is AI-assisted and reviewed by a human who mostly reads the
tests. Therefore:

* Descriptive function and variable names; a comment wherever intent is
  not obvious from the name.
* Moderate file sizes (split before ~300 lines); clear folder structure.
* Extensive tests. **Comments in test code assume a complete beginner**:
  say what the test sets up, what it does, and why the assertion proves
  the behaviour.
* No cleverness that saves lines at the cost of readability.
* **A client never breaks because the server added a field:** in
  `shared/models`, what the orchestrator receives (requests, uploads,
  heartbeats, worker reports) derives from `ReceivedByServer`
  (`extra="forbid"`), everything a worker, the studio or the review UI
  reads from it derives from `ReadByClients` (`extra="ignore"`), and each
  model's docstring says which.
* **Test credentials are obvious placeholders in one place.** Every username,
  password, token or key a test needs comes from
  `shared/testing/credentials.py`, named and valued so nobody (and no secret
  scanner) can mistake it for a real one (`PLACEHOLDER_PASSWORD =
  "placeholder-password-for-tests"`). No inline `password="..."` literals in
  tests; a test enforces it. `.gitguardian.yaml` ignores `tests/**` and that
  module for `ggshield`; the GitGuardian GitHub check is set to Neutral for
  this repo and test findings are dismissed as "test credential". CI
  scripts (`installer/ci/*.ps1`) generate any password they need at run
  time; the same test checks them for password literals.

## 15. Wave-1 reconciliation (2026-10-25, PRs #1–#3)

Decisions that resolve differences between the contract, tailnet and VPS
PRs. Where a PR disagrees with this section, this section wins.

* **Media retrieval.** No MinIO/S3 and no base64 in the webhook. The
  orchestrator streams the encrypted document from the WhatsApp CDN
  (`documentMessage.url` / `directPath`) and decrypts while streaming
  (AES-256-CBC; keys from HKDF-SHA256 over `mediaKey` with info
  `WhatsApp Document Keys`), verifying `fileEncSha256` on the ciphertext
  and `fileSha256` on the plaintext. `mediaUrl` is not used. Reference
  implementation: Baileys `downloadContentFromMessage`.
* **Evolution webhooks.** Instance webhook → orchestrator
  (`POST /intake/evolution-webhook`, header `X-Webhook-Secret`, reply 2xx
  fast, download asynchronously). No global webhook; nothing else consumes
  this instance. Bot replies via Evolution `sendText`. Image pinned `evoapicloud/evolution-api:v2.3.7`.
* **File serving.** Caddy serves `/inbox/*` and `/outbox/*` on the tailnet
  listener with `forward_auth` to `GET /auth/file` on the orchestrator
  (bearer in `Authorization`, path in `X-Forwarded-Uri`; 2xx allows).
  Task offers from `/workers/{id}/next` carry the absolute download URL;
  there is no `/jobs/{id}/raw` or `/outbox/{id}` byte endpoint on the
  orchestrator. Writers drop in-progress files as `<name>.part` (plus
  `<name>.part.json` for resume state) and rename when complete; Caddy hides
  `*.part`, `*.part.json` and dot-files.
* **Reviewer auth.** Review UI is public behind Caddy Basic auth. One file
  of named reviewers at `/srv/pipeline/reviewers.txt` (`BVP_REVIEWERS_FILE`)
  is read by both Caddy (for `/review*`, `/proxies/*`, `/thumbs/*`) and the
  orchestrator (`reviewerBasic`); Caddy forwards `Authorization` and adds
  `X-Reviewer`. **Format:** one `username bcrypt-hash` pair per line,
  separated by a space, `#` starts a comment (Caddy cannot import real
  htpasswd `user:hash` lines).
* **`/enroll`** is routed publicly on the 443 site; the orchestrator
  rate-limits it per source IP itself (Caddy has no built-in limiter).
* **Peer and studio file servers** (8710, 8720) stay unauthenticated in v1;
  the tailnet ACL is the boundary. Short-lived fetch tokens are a later
  hardening item.
* **Env and paths.** All orchestrator settings are `BVP_*`.
  `BVP_DATA_DIR=/srv/pipeline` holds `inbox/ proxies/ thumbs/ outbox/
  db/bvp.sqlite3`. Caps: `BVP_INBOX_HOLD_GB=22` (soft: bot asks senders to
  hold), `BVP_INBOX_CAP_GB=25` (hard), `BVP_PROXY_EVICT_MIN_FREE_GB=10`.
* **Contract decisions accepted as-is** from PR #3: `/next` offers,
  `/claim` reserves; 45 s lease TTL with 15 s heartbeats; chunked resumable
  uploads with `Upload-Offset/Length/SHA256`; studio ACKs direct pushes;
  `PUT /jobs/{id}/thumbnail` (worker-made) and `GET /health`; optimistic
  `expected_version` on review updates; generated OpenAPI checked in CI;
  `tests/<package>/` layout; ffmpeg settings in PR #3 §29–31.
* **HDR footage.** The worker probes `color_transfer`; for `smpte2084` or
  `arib-std-b67` it tone-maps to BT.709 (zscale + tonemap) before the
  yuv420p conversion, for proxy and final render alike.
* **Port correction.** Worker file server is **8710** (PR #3 said 8770).

## 16. Contract PR #3 final decisions (2026-10-26)

* Media keys follow Baileys: `WhatsApp Document Keys` for documents,
  `WhatsApp Video Keys` for the `low_res` video case; `fileEncSha256`
  covers ciphertext plus the 10-byte MAC. Webhook `url` is honoured only
  under `BVP_WHATSAPP_MEDIA_BASE_URL`, else rebuilt from `directPath`.
* Webhook answers 202 immediately; the job exists only after every check
  passed, otherwise the bot asks for a resend.
* `/enroll`: 10 requests per source IP per 600 s → 429 `rate_limited` with
  `Retry-After`. Source IP from the last `X-Forwarded-For` hop, trusted
  only from `BVP_TRUSTED_PROXY_CIDRS`.
* Offers: `download_raw` carries `download_url` + `fallback_download_urls`
  (peers on 8710); `deliver` carries `download_url`. `/auth/file` allows
  `/inbox/<job>.<ext>` only to a worker with a `download_raw` lease on that
  job, `/outbox/<job>.mp4` to the studio only.
* Heartbeat answers carry `deletable_job_ids` so holders purge raws of
  discarded jobs.
* `TAILSCALE_*` env names are the one exception to the `BVP_*` rule.
* Studio receiver is its own server on 8720; peer file server on 8710.
* Review UI runs as its own service (`review_ui/` package, port 8001 on the
  compose network, Caddy routes `/review*` to it) and talks to the
  orchestrator over the HTTP API as a reviewer. This keeps it independent
  of the orchestrator's internals and testable against a fake orchestrator.


## 17. Wave-2 integration decisions (2026-10-27, PRs #4–#6)

Rulings on the open questions raised by the orchestrator, worker and
review-UI PRs. Contract amendments here are implemented by the integration
pass and regenerated into `orchestrator/openapi.yaml`.

### Contract amendments
* `GET /jobs/{job_id}/events` and `GET /events?worker_id=&since=&limit=`
  (reviewer or admin): the orchestrator's event log, oldest first.
* `Job.frame_rate` (float, nullable): set by the first holder from ffprobe
  when it uploads the proxy (`PUT /jobs/{id}/proxy` gains a `frame_rate`
  field). The review UI steps frames with it; falls back to 30.
* `ProcessTask` offers carry `delivery: {mode: "direct"|"outbox",
  studio_url: str|null}`. The orchestrator decides (direct when a studio is
  online); the worker never infers it from `GET /workers`.
* `/enroll` response gains `tailnet_base_url`. The worker sends its bearer
  token to exactly two origins: `orchestrator_url` and `tailnet_base_url`.
* Master backfill needs no new query: the orchestrator keeps offering
  `download_raw` to the master for every job it does not hold — any state
  except `discarded`, including `done` and `failed` — lowest priority after
  live work. The worker's manifest-based workaround is removed.
* `POST /jobs/{id}/failed` gains `scope: "job"|"worker"`. `worker` means
  "this worker could not do it" (disk, local ffmpeg, network): the worker
  is excluded from that task on that job and the task is re-offered; the
  job itself fails only after two different workers report `worker` scope
  for the same task. `job` means the input is bad (hash mismatch at the
  source, undecodable file): the job fails at once. Default `worker`.

### Behaviour confirmed as built
* A heartbeat whose `held_job_ids` omits a raw drops that holder (and logs
  an event). Reprocessing of a job whose holders are offline waits and
  says so; a non-holder fetching from a returning holder is not built.
* In-memory `/enroll` rate limiter is fine (single process).
* Worker-detected bugs are reported as `failed` (scope `worker`).
* Worker keeps a `.part` for 24 h after a lost lease and resumes if the
  same job is offered again.
* Studio persists pending ACKs to a small JSON file and replays them.
* Bearer token in `worker.json` is plaintext; the installer restricts the
  working folder's ACL to SYSTEM + Administrators. DPAPI is a later item.
* Port 8700: worker setup/status page, localhost only (listed in the port
  table as not-on-tailnet). 2 GiB free-disk reserve under the cap.
* "Re-render proxy" button is always available.

### Team lookup
The Baja app has no documented teams API yet. `shared`/orchestrator get a
small adapter with two sources, both optional and neither required at
startup: `BVP_TEAMS_FILE` (CSV `car_number,team_name,school`, reloaded on
change) and `BVP_BAJA_APP_URL` (`GET /api/teams/{car}` as implemented,
lenient fields). File first, then API, then "unknown". The real API path is
confirmed later by João.

### Infra and naming
* Reviewers file is **`/srv/pipeline/reviewers.txt`** everywhere (it is not
  htpasswd format, so the `.htpasswd` name goes).
* Compose: `REVIEW_UI_IMAGE`, `BVP_TEAMS_FILE`, `BVP_BAJA_APP_URL`
  (optional) added; `BVP_EVOLUTION_API_KEY` is the canonical name
  (`_INSTANCE_TOKEN` accepted as alias); runbook creates
  `/srv/pipeline/{inbox,proxies,thumbs,outbox,db}` owned by uid 10001.
* CI builds and pushes `ghcr.io/jbresolin/baja-orchestrator` and
  `ghcr.io/jbresolin/baja-review-ui` on pushes to `main` (tag `main` and
  the short SHA) and on `v*` tags.
* Root README folder map and §13 updated: review UI is its own package.

## 18. Integration PR #7 amendments (2026-10-27)

* Frame rate travels as a `Frame-Rate` header on the chunked proxy upload
  (there is no JSON body on that POST).
* Unknown team → `team_name` empty and `GET /teams/{car}` → 404. The
  literal "unknown" is never stored, so it cannot reach the overlay or
  `latest.m3u`.
* Failure scope exceptions: the master's `download_raw` and the studio's
  outbox pull report nothing on a network failure and simply let the lease
  expire, because a `worker`-scope report would bar the only machine that
  can do that task. Everything else follows §17.
* Known gap: `worker/README.md` local-dev recipe points workers at a bare
  orchestrator, which does not serve raw bytes (Caddy does). Fixed in wave 3
  by a dev-only static file route behind `BVP_DEV_MODE=1`.

## 19. Wave-3 decisions (2026-10-27, PR #9)

* Intake ignores group chats entirely (no job, no reply); DMs only, per §3.
* `reviewers.txt` is reloaded on change, like `teams.csv`; no restart to
  add or remove a reviewer.
* Dev mode (`BVP_DEV_MODE=1`, localhost only) serves inbox/outbox/proxies/
  thumbs from the orchestrator with the Caddy rules, so the local recipe
  needs no Caddy.
* `GET /metrics` (admin bearer, Prometheus text) added; contract 1.1.0.
  Counters reset on restart; no Prometheus/Grafana in compose.
* Nightly `backup.sh` via systemd timer, 14 dated folders, `.env` excluded.
* A good document gets "✅ Vídeo recebido! Obrigado." once per video.
* The smoke script probes the public side with an empty `POST /enroll`
  expecting 400 (one of the VPS IP's 10 tries per 10 min). Accepted.

## 20. Installer (PR #8) and VPS stack split (2026-10-27)

Installer, as built:
* `BajaWorker.exe` (PyInstaller onedir + Inno Setup), service `BajaWorker`
  under NSSM, install dir `C:\Program Files\BajaWorker`, data `C:\BajaWorker`
  (ACL: SYSTEM + Administrators), firewall rules for 8710/8720, setup page
  on localhost:8700. Uninstall keeps `C:\BajaWorker`.
* First run is `BajaWorker.exe --first-run` (frozen, Linux-tested with a
  fake `tailscale`); re-runnable from the Start menu.
* Tailnet hostname `baja-w-<8 chars of worker id>` or `baja-studio`
  (generic on purpose: hostnames can reach public certificate logs).
* Vendor payloads pinned by SHA-256 in `installer/vendor.lock`, with mirror
  fallbacks checked against the same hash.
* Enroll URL baked at build time from the repository variable
  `BVP_ENROLL_URL`; `v*` builds fail without it. Set it before release 1.
* Not yet verified on a physical Windows PC: SmartScreen/UAC flow, real
  `tailscale up`, boot ordering, NVENC, upgrade over an enrolled install.

VPS layout (supersedes the single compose in §2/§15):
* The staging VPS runs three stacks. **Base** (repo `jbresolin/baja-vps`,
  always on): Caddy on host 80/443 importing `/srv/caddy/conf.d/*.caddy`,
  Evolution, Postgres, Redis, Evolution backups, external network
  `baja-edge`. **Scoring staging** (repo `baja-sae-brasil-online`): nginx
  unpublished, joins `baja-edge`, reached by Caddy at `baja-nginx:80` via a
  permanent compose override. **Pipeline** (this repo, up only around
  events): orchestrator + review UI on `baja-edge`, its own small Caddy for
  the tailnet listener on `TAILSCALE_IP:8080`, `up.sh`/`down.sh` that
  install/remove `conf.d/pipeline.caddy`, reload Caddy and toggle the
  Evolution instance webhook.
* If the scoring stack is down Caddy answers 502 for its hostnames;
  accepted on a staging box.
* Cloudflare Tunnel was considered and rejected for the public side
  (concern about tunnel limitations); Caddy terminates TLS.

Pipeline stack as built (`infra/vps/`, follow-up to PR #9):
* `docker-compose.yml` (project `baja-pipeline`): orchestrator and review UI
  on `baja-edge` with aliases `bvp-orchestrator` / `bvp-review-ui` (a bare
  `orchestrator` could collide on a shared network), plus a private
  `pipeline` network to `pipeline-edge` (Caddy, `Caddyfile.tailnet`,
  published on `${TAILSCALE_IP}:8080` only).
* `conf.d/pipeline.caddy` is a template; `up.sh` renders it with
  `envsubst '${PIPELINE_DOMAIN} ${CADDY_PIPELINE_DIR}'` because the base
  Caddy's own environment is the base repo's. The base Caddy serves
  `proxies/`/`thumbs/` and reads `reviewers.txt` from a read-only mount of
  `/srv/pipeline` (a requirement on the base stack). A reload the base Caddy
  refuses rolls the file back, so it never blocks other stacks' reloads.
* The webhook is set with `POST /webhook/set/{instance}` (v2.3.7 has no PUT
  route) using the instance token, so the global Evolution key is not in
  this repo. `down.sh` disables it but leaves a webhook pointing at another
  URL alone.
* `baja-edge` is `10.201.0.0/24` (the base stack pins it; this compose joins
  it as external with no subnet). The pipeline's own network is pinned to
  `10.202.0.0/24`. `BVP_TRUSTED_PROXY_CIDRS=127.0.0.1/32,10.201.0.0/24,10.202.0.0/24`
  in `.env`; the orchestrator's default also gained `10.201.0.0/24`. The
  smoke test fails if a `baja-edge` subnet is not covered.
* The pipeline hostname is DNS-only at Cloudflare (grey cloud): HTTP-01
  certificates, no Cloudflare-only matcher, the TCP peer is the real client.
* Backups cover only the pipeline (SQLite, reviewers, teams); the timer's
  `ExecCondition` skips nights when the orchestrator container does not exist.

### §20 addendum — edge network and Cloudflare (2026-10-28)
* `baja-edge` is an external network on the fixed subnet **10.201.0.0/24**
  (outside Docker's default pools, 172.16/12, 192.168/16 and Tailscale's
  100.64/10), created by the base stack.
* Staging hostnames stay Cloudflare-proxied (orange): their vhosts accept
  connections only from Cloudflare's IPv4+IPv6 ranges (`remote_ip`), use the
  same ranges as `trusted_proxies`, pass `CF-Connecting-IP` through
  unchanged and forward `X-Forwarded-For/-Proto`; nginx trusts only the
  `baja-edge` subnet as proxy. Certificates via DNS-01 (caddy-dns/cloudflare,
  custom Caddy build) with Cloudflare SSL mode Full (strict).
* Pipeline hostnames are DNS-only (grey): open vhosts, ordinary HTTP-01.
* Pipeline: `BVP_TRUSTED_PROXY_CIDRS` must include `10.201.0.0/24` (the base
  Caddy's address) and the pipeline's own compose network; the `/enroll`
  rate limiter reads the last `X-Forwarded-For` hop as before.

### §20 addendum 2 — stack split as built (2026-10-28, baja-vps PR + PR #10)
* Pipeline's own compose network: **10.202.0.0/24**; services answer on
  `baja-edge` as `bvp-orchestrator` and `bvp-review-ui`.
* Base Caddy is a custom build `baja-caddy:2.11.7-cf0.2.4` (caddy-dns/
  cloudflare v0.2.4). Cloudflare ranges are a static list in
  `caddy/cloudflare-ips.env`, feeding both the `remote_ip` gate and
  `trusted_proxies`; `refresh-cloudflare-ips.sh` updates it.
* Staging vhosts: apex, api, certificado, fila, juiz, resultados, forum
  under BAJA_DOMAIN, plus FORMULA_DOMAIN. `www` is not served (no nginx
  template). Port 80 is ungated (HTTPS redirect only).
* Evolution webhook is set with `POST /webhook/set/{instance}` using the
  instance token (`EVOLUTION_URL` + instance token in the pipeline `.env`;
  the global Evolution key never leaves the base stack).
* Caddy reload: `docker exec caddy caddy reload --config /etc/caddy/Caddyfile`.
* Scoring repo must add `set_real_ip_from 10.201.0.0/24` with
  `real_ip_header CF-Connecting-IP` in its staging override.

### §20 addendum 3 — base stack PR #1 (2026-10-29)
* Evolution Manager/API is tailnet-only on `TAILSCALE_IP:8081` (admins
  only in the ACL; workers never see it). Pipeline worker API stays on 8080.
* Scoring staging needs a third `LISTEN_MODE` (plain HTTP behind a
  TLS-terminating proxy, `https://` links, trusts `X-Forwarded-Proto`);
  `listen-tls` loops and `listen-http` yields `http://` links. Owned by the
  scoring-repo override PR.
* Event days: the scoring staging stack is stopped (T-7) and restarted
  after; no memory limits added to it.
* Off-VPS copy of the base-stack backups (rclone to the Google Shared Drive
  used by the platform backups): later item, not a blocker.
* Compose project `baja-base` reuses the old `baja_*` volume names so the
  WhatsApp session and certificates survive the switch.


### §8 addendum — shared tailnet (2026-11-03)
Supersedes "Baja tailnet" and `tag:server` above. The pipeline joins the
**existing** Baja tailnet (platform prod + staging, Uptime Kuma, João's
devices). Prod and staging stay untagged member devices; the staging VPS is
addressed by the pre-existing host alias `baja-staging`. Isolation: the
tailnet's allow-all rule is `autogroup:member → autogroup:member` and tagged
devices are not members, so `tag:worker`/`tag:studio` nodes reach only what
`infra/tailnet/acl.hujson` grants (staging:8080, worker:8710, studio:8720)
and are reached only by admins. The policy's tests assert workers are denied
prod, staging SSH/443/8081 and the monitor. `acl.hujson` is now the complete
policy file. Key expiry must be disabled on `baja-teste1` (staging).

## 21. Admin page (LIVESTACK-37, 2026-11-05)

Everything the operator used to configure by editing files on the VPS or
by curl is a page under `/review/admin/` (pt-BR, admins only). Only
secrets are edited on the server. **Supersedes** §15 "Reviewer auth", §17
"Team lookup" and the `reviewers.txt`/`teams.csv` reload rule of §19.

* **Login in the app.** Users (bcrypt hash, role `reviewer`/`admin`,
  disabled flag) live in the orchestrator's DB. `POST /auth/login` issues a
  session (only its SHA-256 stored, 12 h idle timeout); the review UI keeps
  it in the `bvp_session` cookie (httpOnly, Secure, SameSite=Lax, path `/`)
  and forwards it as `sessionBearer`. Caddy has no `basic_auth`;
  `/proxies/*` and `/thumbs/*` use `forward_auth` to `GET /auth/media`.
  `reviewers.txt` is imported once into an empty users table
  (`BVP_BOOTSTRAP_ADMIN` becomes admin), then ignored. Contract 2.0.0.
* **Bare domain.** `redir /review/ 302` was parsed as matcher `/review/`
  and target `302`, so `/` got an empty 200; now `redir * /review/ 302`.
* **Contacts.** Every DM sender is recorded (`unknown`). Intake mode
  `open`/`allowlist`; blocked senders get nothing, unallowed ones in
  allowlist mode one `SENDER_NOT_AUTHORIZED` per 24 h (videos only). A
  contact's default prova is filled into their jobs.
* **Provas and teams from the Baja results API** (brief amendment
  2026-11-04), for the event id set in Settings: on change, on the Sync
  button, every 6 h. Keyed by API prova id / car number; adds and renames,
  never deletes (missing rows become inactive). Manual provas stay, listed
  after synced ones; a manual correction of a synced team is an override
  kept across syncs. A prova's slug is fixed at creation, so a rename keeps
  the clip folder; `ready`/`reprocess` need a prova from the table
  (`unknown_prova`). The overlay uses the team's `short_name`. The
  `BVP_BAJA_APP_URL` adapter and any teams CSV upload are gone.
* **Settings** (intake mode, inbox soft cap, proxy eviction threshold,
  installer URL, event id, results API URL) are stored in the DB; `.env`
  gives first-start defaults only. Secrets show as configured/missing.
* **Enrollment and workers.** Tokens are listed (unused/used/expired/
  revoked) and unused ones revoked; a revoked worker's token answers 401,
  its leases end and its holder rows are dropped. Tailnet removal is manual.
* **Audit.** Every admin change is an event with `details: {before,
  after}`; each page shows its latest changes (`GET /admin/audit`).

## 22. After the first real video (LIVESTACK-38, 2026-11-04)

The first real video went end to end on the production VPS on 2026-11-04.
Fixes and additions, with the decisions taken while building them (the PR
"Review and bot fixes from the first real video" lists them in full):

* **Review version.** `Job.version` only moves on reviewer actions (any
  `PATCH …/review`, including the state change it causes); machine
  activity never bumps it, so a busy worker no longer makes every save a
  `version_conflict`. `regenerate-proxy` does not bump it either.
* **Media info.** The first holder sends the raw's whole ffprobe report
  (`PUT /jobs/{id}/media-info`, contract 2.1.0); every location/GPS tag is
  removed on the worker and again on the orchestrator, only
  `location_present` is kept. The job page shows it under the player.
* **Bot answers on the video.** ✅/⚠️/❌ reactions on the volunteer's
  message (its key as it arrived, `@lid` included); every text is a quoted
  reply to that video, sent to the phone JID. A ⚠️ is never replaced by ✅.
* **Discard can be undone** (`restore`, back to `discarded_from`). Nothing
  is deleted during the discard grace period (24 h, inbox copy included;
  editable on the admin Settings page, fixed on the job at discard time);
  after it, restore works while a copy is left, else `raw_unavailable`.
  `discarded` is therefore no longer a final state.
* **Clips are the archive** (§11 as amended): workers keep their clips,
  the master archives every version (`archive_clip`), outbox copies wait
  for ACK + archive (72 h at most, also a Settings value), signed-in users
  download outbox clips at `/clip-downloads/*` (Caddy `forward_auth` to
  `/auth/media`, like proxies) and see where each clip is.
* **Clip overlay** drawn with Pillow in BajaOvrLay's Ardósia lower-third
  look, from a vendored `tokens.css` and Bebas Neue (`worker/overlay_theme/`).
* **Worker for novices:** a tray icon (`BajaWorkerTray.exe`), pause
  (`paused` in the heartbeat: no offers, still serves peers), stop/start
  without UAC (service rights for Users), a pt-BR status page and a local
  JSON API on 127.0.0.1:8700; a pending tailnet join is explained in pt-BR
  instead of a `ConnectError`.

## 23. Admin polish from real use (LIVESTACK-42, 2026-11-05)

After a day of real use (brief "05 — Worker and admin polish from real
use", items 6-11; the worker items are LIVESTACK-41). Contract 2.2.0.

* **Names on the clips.** Settings has two independent choices, team
  full/short and school full/sigla (default short/sigla, the old look); a
  missing name falls back to the other. Applied when a render is offered
  (`domain/clip_names.py`), so a change affects every clip rendered
  afterwards, and in what the UI shows for a clip (job page, clips page).
  `ProcessTask.school_short` carries the full school name under "completo"
  rather than adding a field, so workers need no change. `latest.m3u` keeps
  the full team name. Migration 0008 stores the full school name on jobs;
  older jobs fall back to the sigla.
* **Worker display name.** `Worker.name` is the display name (typed on the
  token form, prefilled with the host name; changed on Admin → Workers);
  `Worker.hostname` is shown beside it.
* **One master, switched in the UI.** "Tornar master" promotes a worker and
  demotes the master in one step; only master ↔ worker (a studio has
  tag:studio and enrolls again). Workers send their running role in the
  heartbeat and get the orchestrator's back (never to workers that do not
  send it, since older parsers refuse unknown fields); on a difference they
  save it and restart through NSSM. Deploy the orchestrator before workers
  that send `role`.
* **History out of the way.** Revoked workers are off Frota and folded at
  the bottom of Admin → Workers; spent tokens are folded on Cadastro de PCs.
* **Banners** keep their text in a `<p>`; only a banner with a button beside
  its text is a flex row.
* **In-place admin toggles.** Provas activate/order and contacts
  allow/block answer with re-rendered rows over JSON; no reload.

## 24. Worker polish from real use (LIVESTACK-41, 2026-11-05)

João upgraded his desktop worker to 0.2.0, re-enrolled it and ran the
first studio. The worker-side fixes (brief "05 — Worker and admin polish
from real use", items 1–5 and 12–15; installer 0.3.0, contract 2.3.0):

* **The status page always comes up** (`worker/lifecycle.py`). Page and
  local API start first, unconditionally; then visible phases: broken
  `worker.json` (shown with file, line, column and a pt-BR hint, never a
  crash), token form, studio clip folder, waiting for the tailnet address
  ("Esta máquina não está conectada à rede Baja.", and explicitly when the
  saved address is not this PC's any more), running. `BajaWorker.exe
  --service` no longer waits for Tailscale before starting the worker.
* **"Registrar de novo"** (page and tray) clears only the identity and
  returns to the token form in the same process; offered automatically
  when a heartbeat gets 401 (revoked).
* **"Usar a rede atual"** (`--first-run`, non-studio, only when Caddy's
  tailnet listener already answers `/health`): the PC stays on its own
  tailnet, heartbeats say `shared_tailnet`, and the orchestrator never
  makes it a second holder or a peer source and tells it "outbox".
* **Studio clip folder** chosen at setup (default `C:\BajaClips`), Users
  get inherited read access (by SID); the page shows `latest.m3u` for OBS.
* **The page never reloads**: only its live region is refreshed in place;
  a form in use is never replaced.
* **File names people can read**: clips `<prova>/<car>/<prova>-<car 3
  digits>-p<passagem>[-vN].mp4` everywhere (studio, archive, kept clips,
  downloads); raws on workers `<sender>-<YYYYMMDD-HHMMSS>-<job id first
  8>.<ext>`, fixed at intake; the VPS inbox keeps `<job_id>.<ext>`.
  Nothing on disk is renamed.
* **Delivery to the studio is idempotent and lock-tolerant**: same content
  = delivered, locks retried, a locked clip on air is never overwritten
  (new version beside it as `-v2`). The double delivery came from the
  worker's 60 s outbox fallback racing a late studio ACK; the worker now
  checks the job first. Workers probe the studio (3 s) before pushing and
  remember an unreachable one for 5 minutes.
* **"Baixar original"**: task kind `upload_original`, temporary
  `originals/` on the VPS (24 h after the last request, 10 GB cap, both
  Settings values), served by Caddy under the raw's readable name.

## 25. Edits after delivery (LIVESTACK-43, 2026-11-06)

João changed the cut-in of a job whose clip was already on the studio
box: the save worked, but nothing said the clip on air no longer matched,
and no re-render followed (brief "06 — Edits after delivery are visible
and reprocessable"). Contract 2.4.0.

* **Clips remember their render inputs.** Each clip version keeps what it
  was rendered from (cuts, review fields, name choices and the overlay
  texts they give, overlay theme version = SHA-256 of the worker's
  `tokens.css`, the raw's sha256). The worker reports them
  (`PUT /jobs/{id}/render-inputs`) before delivering; the orchestrator
  snapshots the task it handed out at every process claim, which supplies
  the name choices (workers only see chosen names) and stands in when a
  worker reports nothing. No `ProcessTask` field was added; but `Job`
  gained `clip_inputs` (and `ClipVersion.render_inputs`), and workers
  before this one refuse unknown fields when they read `GET /jobs/{id}`
  (studio playlist titles, outbox deliver, the direct-push fallback
  check). So, as for 2.3.0, **upgrade the workers together with the
  orchestrator** this time. From this version on, clients ignore fields
  they do not know (§14), so later additions need no lockstep upgrade.
* **Mismatch is computed by the orchestrator** (it knows the Settings),
  newest version against the review now: `Job.clip_inputs`,
  `ManifestEntry.clip_inputs`, `JobSummary.clip_outdated`,
  `GET /review/queue?outdated=true`. Texts are compared, not choices, so a
  choice that draws the same name is no difference. Clips rendered before
  this have no recorded inputs and show no warning rather than a guessed one.
* **Reprocess is the obvious action after delivery.** "Salvar e
  reprocessar" is the existing `reprocess` action (it always saved the
  fields it carries), now the primary button whenever a clip exists; it
  was already offered for enviado/entregue/concluído on main, but looked
  like a second-rank button next to "Salvar". The next rendering is
  delivered under its `-vN` name, so the version on air is never written
  over; `latest.m3u` moves to it. With no holder online the reprocess is
  accepted and the job says it waits for one, at once.


## 26. First test of 0.3.0 (2026-11-06)

Fixed on main after João's first end-to-end test of installer 0.3.0.

* **Cuts are optional.** Many videos need no trimming. On `ready` and
  `reprocess` an empty cut-in is stored as 0 and an empty cut-out as the
  probed duration, so the job, the render inputs and the mismatch check
  all show the cut that is rendered. `save` leaves them empty. The job
  page's empty cut fields say "início do vídeo" / "fim do vídeo".
* **Refused task kinds are asked for again after 5 minutes.** A worker
  that met an orchestrator older than one of its task kinds stopped asking
  for that kind until it was restarted. With the usual order (workers
  first, then the VPS), "Baixar original" then stayed on "Preparando"
  for good.
* **Outbox wording.** A clip in the outbox is "esperando o estúdio
  buscar", not "estúdio offline": a worker on a shared tailnet always
  delivers through the outbox while the studio is online.
* **Frota.** The studio is never "perto do limite" (it takes no raws, so
  the orchestrator always reports `accepting_new_raws: false` for it), and
  the online count leaves out revoked workers, as the list already does.
* **The studio's outbox ACK reaches the orchestrator.** Caddy's tailnet
  listener sent every `/outbox/*` request to the file check, including the
  studio's `POST /outbox/<job_id>/ack`, which it refused with 403. A clip
  pulled from the outbox was stored but never confirmed, and the studio
  retried every lease (45 s). Only GET/HEAD go to the file check now. The
  end-to-end tests route the workers' API calls through the fake Caddy, as
  on the VPS, and cover the outbox route. `up.sh` recreates `pipeline-edge`
  every time, so a pulled `Caddyfile.tailnet` is really loaded.
* **The :8700 page shows the installer's version** (it showed the Python
  package's "0.1.0" on every build).
* **Shared-tailnet workers are offered the direct push again.** LIVESTACK-41
  item 4 sent every clip from a "rede compartilhada" worker to the outbox,
  on the assumption that it cannot reach the studio. João's desktop can
  (the studio is shared into his tailnet), and pushed directly until the
  VPS was updated. The orchestrator cannot tell, so it offers the push and
  the worker's 3 s `/health` check (item 13) falls back to the outbox.

## 27. Help numbering the passagens (LIVESTACK-45, 2026-11-06)

A reviewer must say whether a video is a car's 1st, 2nd or nth passagem in
a prova, and nothing on screen helped: the queue was in arrival order,
which is not filming order, and the job page showed no other video of the
car (brief "08 — Help reviewers number the passagens"). Contract 2.5.0.

* **Every job has a recording time** (`Job.recorded_at` and
  `recorded_at_source`, also on `JobSummary`): the raw's `creation_time`
  (format tags, else the first video stream's) when plausible (not after
  the bot received it, not more than 7 days before), else the WhatsApp
  send time (`source.sent_at`, now stored), else the time received
  (`source.received_at`, the webhook's arrival, not the job's creation
  after the download). Rules in `orchestrator/domain/recording_time.py`.
  The send time gets the same plausibility test. A `creation_time` without
  a zone is UTC; one with an offset is converted.
* **No worker change.** The first holder already sends the whole ffprobe
  report (`PUT /jobs/{id}/media-info`, §22), tags included; the
  orchestrator reads `creation_time` from it when it arrives. 0.3.0
  workers need nothing, and they ignore the new answer fields (§14).
* **Older jobs.** Migration 0011 gives every job its creation as
  recording time (`received`); the first start then reads the probe report
  already stored, once (`first_start.backfill_recorded_at`), without
  touching `updated_at`.
* **The review queue is in filming order**: `GET /review/queue` sorts by
  `recorded_at`, then arrival, then id, for every state filter (the clips
  page's outbox list uses the same call; its order does not matter there).
  Each row says "gravado 14:02" (São Paulo time, with the date when not
  today) with a small "envio" or "chegada" marker when it is not the
  file's own time.
* **One car's videos:** `GET /review/car-videos?prova=…&car_number=…`
  (reviewer session) lists them, discarded ones left out, in filming
  order. Jobs are matched by the prova's clip folder (slug), and `prova`
  may be a name or a slug, so a renamed prova, or a page still showing
  the old name, finds the same videos.
* **The suggestion and the warnings are computed by the review UI**, in
  Python (`review_ui/passagem_help_view.py`), from that list and the form
  values; the job page asks `GET /review/api/jobs/{id}/passagem-help` on
  every change of prova, car or passagem (debounced) and swaps the panel
  in, never reloading. Suggestion: one more than the passagem of the last
  numbered video filmed before this one; 1 when none was; **no
  suggestion** when that number is already another video's (the text then
  says which, so the reviewer checks both instead of clicking a wrong
  number). It is applied only by the "Usar passagem N" button. Warnings:
  one per other video with the same number; at most one per direction for
  the order (the first video filmed after this one with a lower number,
  the last one filmed before it with a higher number).
* **Warnings never block.** They show under the passagem field and in a
  box above the action buttons; "Pronto" and "Salvar e reprocessar" ask
  for a confirmation listing them, and go ahead on OK.
* Times in the panel show the date only when not on this video's day; the
  order is called approximate when any time in it is a send or arrival
  time. The job page's header says when the video was filmed and how that
  is known.
* **Not handled:** cameras that write local time as if it were UTC (some
  action cams) look 3 h early and pass the plausibility test; a phone's
  `com.apple.quicktime.creationdate` (local time with offset) is not read.
  Matching passagens to official results and automatic numbering stay out
  of scope.

## 28. Welcome message for new contacts (LIVESTACK-44, 2026-11-06)

When João adds a filmmaker on Admin → Contatos, the bot now tells them
which number it is and how to send videos (brief "07 — Welcome message
for new contacts"). This also answers §12 open question 1 for anyone
added by hand. Part of contract 2.5.0, with LIVESTACK-45 (§27); neither was
deployed before both merged.

* **One text, a setting.** `AdminSettings.welcome_message`, edited on
  Configurações, stored with the other settings (no `.env` value; the
  shipped pt-BR default lives in `shared/welcome_message.py`, seeded on
  first start like every new key). Stored with `\n` line ends and the ends
  trimmed; at most 2000 characters. Empty = feature off.
* **Placeholders.** `{nome}` = the contact's display name, `{prova}` = their
  default prova; nothing when missing, and then the one space and a comma
  right before the placeholder go too ("Olá {nome}!" and "Olá, {nome}!" →
  "Olá!"). Anything else in braces stays as typed. The same rule in Python (sending, server-side preview)
  and `review_ui/static/js/welcome.js` (live preview), tested on the same
  cases. The preview uses the example values "Maria Conceição" / "Enduro".
* **Sending.** `CreateContactRequest.send_welcome` (default false, so
  scripts behave as before) → after the contact is committed the
  orchestrator sends the filled text with Evolution `sendText` to the
  contact's number (not a quoted reply). The answer is `ContactCreated`
  (a `Contact` plus `welcome: {sent, error}`); a failed send never undoes
  the contact. `POST /admin/contacts/{phone}/welcome` sends it again and
  answers 200 with the same `welcome` even when Evolution refused (the
  attempt happened); 409 `conflict` for a blocked contact or an empty
  message, 404 for an unknown contact.
* **Never to blocked contacts, never with an empty message.** On create
  these are not attempts: nothing is sent, no event, `welcome.error` says
  why. The page disables the controls in both cases; the orchestrator
  enforces it anyway (a page without JavaScript keeps the box ticked).
* **Record.** Every attempt is an admin event, `welcome_sent` or
  `welcome_failed` (actor = the admin, `details.after` = phone, text as
  sent, and Evolution's reason), shown under "Últimas alterações" on
  Contatos. `contacts.welcome_sent_at` (migration 0012) is the last time
  Evolution accepted it, shown on the row; failures are only events.
* **Evolution's reason** is what `sendText` answered (`HTTP 500 {…}`) or the
  connection error. The page shows it inside `lang="en"` in a yellow
  banner: "Contato criado, mas a mensagem não foi enviada: …" on create,
  "A mensagem de boas-vindas não foi enviada: …" from the row. That page is
  rendered in answer to the POST (not a redirect), because the reason
  cannot travel in `?feito=`.
* **Contacts page.** The add form's box is ticked for "Permitido", unticked
  for "Desconhecido", unticked and disabled for "Bloqueado" (`js/admin.js`).
  Each row has a "Boas-vindas" column: when last sent and "Enviar
  boas-vindas", which asks to confirm. `ContactsResponse.welcome_message_set`
  tells the page whether a message exists, so the Contacts page never asks
  Evolution for the Settings status block.

## 29. Render options: vertical videos and no audio (LIVESTACK-50, 2026-11-07)

A video filmed upright rendered as a 608×1080 strip with the 1920-wide
overlay cut off, and some clips had to go out without their sound (wind,
shouting, music). Brief "11 — Render options: vertical videos and no
audio". Contract 2.6.0, installer 0.4.0.

* **Orientation from the probe already sent.** The first holder's
  media-info report (§22) has the first video stream's size and its
  rotation note (display matrix side data, else the old `rotate` tag).
  The orchestrator stores the size *after* the note
  (`Job.display_width/height`, migration 0013) and `Job.vertical` (after
  the reviewer's "Girar" too); `JobSummary.vertical` marks the queue row.
  Jobs probed before are read once at the next start
  (`first_start.backfill_display_size`), with no bot reply. No worker
  change was needed for this part.
* **Every clip is 1920×1080.** `shared/framing.py` decides from the
  picture after both turns (the file's note, which ffmpeg obeys inside the
  filter graph, then "Girar"). **16:9** = what `scale=-2:1080` makes exactly
  1920 wide (1920×1080, 1280×720, 3840×2160; not 1920×1088 or 854×480):
  rendered exactly as before, byte-identical command, so horizontal clips
  and the golden tests do not change. **Anything else** (upright phone,
  4:3, ultra-wide) is fitted whole at full height or full width and the
  rest of the frame is filled per "Enquadramento":
  * `blurred` ("desfocado", default): the same video covering a 480×270
    canvas, boxblur 12:2, a 45 % black box over it (`drawbox`, any bit
    depth), scaled up to 1920×1080, the fitted video centred on it;
  * `black_bars` ("barras pretas"): `pad` with black;
  * `crop` ("cortar"): a 16:9 band of the video fills the frame: a
    full-width band for a picture taller than 16:9, a full-height one for
    a wider picture; `crop_position` 0…1 places it (0 top/left, 0.5
    middle, the default, 1 bottom/right). The job page draws the band over
    the player.
  The overlay is drawn on top as always, always 1920 wide; HDR is still
  tone-mapped last, on the 1080p picture (the blurred composite keeps 10
  bits until then, `overlay=…:format=auto`). Sizes and offsets are worked
  out in Python and written into the graph as integers, rounded half up
  like ffmpeg and JavaScript.
* **"Girar"** (0/90/180/270 clockwise, `transpose`/flips) runs before
  everything else, for files whose note is wrong. A sideways phone that
  says "upright" and is turned 90° is 16:9 again: only the turn is added
  to the old graph. The job page's player turns with it (CSS), and the
  orientation line and the framing choices follow the field before
  saving; the browser's own player controls are hidden while turned (they
  would turn too), the keyboard and the cut bar still work.
* **"Sem áudio"**: the render takes the path a raw without sound already
  took (`-an`, no loudnorm): the clip has no audio track. The job page
  mutes the proxy while it is ticked.
* **The bot asks for horizontal.** The job's *first* probe report showing
  a vertical picture makes the bot quote that video once with
  `AdminSettings.vertical_video_message` (default "📱 Vídeo na vertical. Da
  próxima vez, grave na horizontal, por favor."; empty = off; trimmed,
  `\n` line ends, 500 characters). A re-rendered proxy or a restart finds
  the report already stored and stays silent; a refused send is the event
  `vertical_reply_failed` and is not retried. The ✅ reaction stays: the
  video is fine and is used. Sent after the worker's PUT is answered.
* **Render inputs.** `rotation`, `framing`, `crop_position` and
  `no_audio` are render inputs (§25; `RenderInputField` `rotation`,
  `framing`, `audio`). Framing and the band are recorded *as they show*:
  none on a 16:9 picture (so "barras pretas" on a landscape clip never
  marks it out of date), the band only for "cortar", compared within half
  a percent. Inputs recorded before this read as no turn and sound kept.
  A vertical clip rendered before 0.4.0 (the narrow strip) therefore shows
  as out of date with "enquadramento: nenhum (16:9) → desfocado": true,
  and a reprocess fixes it.
* **Render features: which workers may render.** A 0.3.0 worker ignores
  task fields it does not know and would render the strip, with sound. So:
  a worker version declares the render features it can do
  (`WorkerCapabilities.render_features` at enrollment, stored in
  `capabilities_json`; `HeartbeatRequest.render_features` in every beat
  replaces it; left out = none); each process task lists
  `required_render_features`; the orchestrator offers (and lets claim) a
  render only to a holder that declared them all
  (`orchestrator/domain/render_features.py`). Today: `framing` (a turn, or
  a picture that is not 16:9) and `no_audio` ("Sem áudio" on a raw with
  sound). A job that needs nothing new goes to any worker, as before.
  While holders are online but none can render it, the job's waiting
  reason says so ("Esperando um PC … Baja Worker 0.4.0 ou mais novo").
  Feature names travel as plain strings, so a newer worker may declare
  one an older orchestrator ignores. **To add one** (LIVESTACK-47 added
  `pass_result`, §32): add it to `RenderFeature` and to the worker's
  `worker/render_features.py`, say in `required_render_features()` when a
  job needs it, bump the installer. Frota shows what each worker declares.
* **Deployment order: the VPS first, then the workers.** The orchestrator
  receives with `extra="forbid"` (§14), so an orchestrator older than
  2.6.0 answers 400 to a heartbeat carrying `render_features`. A 0.4.0
  worker that gets that 400 (naming the field) sends the beat again
  without the key and leaves it out for 5 minutes, then tries again;
  nothing is lost (its leases are renewed by the resent beat). Its
  render-inputs report with the new fields is also refused by an old
  orchestrator; the worker logs it and delivers anyway (§25), and the
  version keeps the claim snapshot. The review UI is deployed with the
  orchestrator (it sends the new review fields).
* **Not built:** stabilisation, zoom/pan inside a horizontal video, per-prova
  defaults for these options. A picture's pixel aspect (SAR) is not
  considered: sizes are in stored pixels, as `scale=-2:1080` did. The
  quote of the vertical reply carries the phone JID as `remoteJid` (the
  job never stored an `@lid` sender's original one); WhatsApp matches
  quotes by message id.

## 30. Reviewer context: received time and the sender's messages (LIVESTACK-49, 2026-11-07)

Filmmakers write what the reviewer needs next to the video ("carro 42,
segunda passagem", a reply "errei, é a 3"), and the bot ignored every
text; the job page showed when a video was filmed but not when it
arrived, which is how people refer to a file (brief "10 — Reviewer
context: received time and the sender's messages"). Part of contract
2.6.0, with LIVESTACK-50 (§29); neither was deployed before both merged.

* **Received time.** The job page header shows, side by side, "Gravado …"
  (with its source, as in §27), "Recebido …" (`IntakeSource.received_at`,
  the webhook's arrival) and "Enviado por …". The queue row and the "Outros
  vídeos" panel say "recebido 14:05" in the recording time's short format
  (`JobSummary.received_at`). In the queue it replaces the relative "há 5
  min" arrival time.
* **What is kept.** Every private message that is not a video, from a
  contact whose videos would be accepted (`contacts.messages_are_kept`: not
  blocked, and allowed when in allowlist mode), except the bot's own:
  text, captions of images and documents, and only the kind for the rest
  (audio, image without caption, sticker, location, contact card,
  "other"). Nothing is downloaded and nothing is answered. Reactions, poll
  votes and WhatsApp bookkeeping (`messageContextInfo`, sender keys) are
  not kept. Table `sender_messages` (migration 0014): WhatsApp id, sender
  JID (the phone JID, also for `@lid` senders, like jobs), received and sent
  time, kind, text (at most 4000 characters), the quoted id. A video's
  caption was **not** kept before (the brief assumed it was); it is now
  `jobs.caption` / `IntakeSource.caption`, kept with the job and not swept.
* **Evolution 2.3.7 shapes**, read from its source
  (`whatsapp.baileys.service.ts`), not yet captured on the VPS:
  `prepareMessage` rewrites an `extendedTextMessage` into `conversation`
  and moves its `contextInfo` up to `data.contextInfo`, so a reply's quoted
  id is `data.contextInfo.stanzaId`. Edits and deletions never arrive as
  `messages.upsert`: any message with a `protocolMessage` is sent as event
  `messages.edited` whose `data` is the protocolMessage (`key.id` = the
  changed message; type 14 / `MESSAGE_EDIT` with `editedMessage`, or 0 /
  `REVOKE`), and a deletion also as `messages.delete` (the key plus
  `status: DELETED`, only when Evolution finds the message in its own
  store). Both are read; the type as a number or a name. The webhook
  therefore subscribes to `MESSAGES_EDITED` and `MESSAGES_DELETE` too
  (`infra/vps/stack_ctl.py`); `up.sh` resets a webhook set without them.
* **Edits and deletions** are matched by message id only (the edited event
  says nothing about who sent it, and WhatsApp lets people change only
  their own messages). An edit replaces the text and sets `edited`; a
  deletion drops the text (not only hides it) and sets `deleted`; a later
  edit does not bring it back. Edits of a video's caption are not applied.
* **Which video a message belongs to** (`domain/sender_messages.py`), per
  sender, by the webhook's arrival time: replies quoting this video,
  whenever sent; "after" = after this video and before the sender's next;
  "before" = after the previous video and before this one (for the first
  video, everything earlier still kept). A reply quoting another of the
  sender's videos is listed only there. Every job counts as a boundary
  whatever its state (a discarded video was still sent). A message at the
  same instant as a video counts as after it. So a message between two
  videos is listed under both, as "after" of one and "before" of the other.
* **Contract.** A separate endpoint, `GET /jobs/{id}/sender-messages`
  (reviewer session only; workers can read the job but never people's
  messages), answers the items with their role in display order: caption,
  replies, after, before. `JobSummary.sender_message_count` (queue and
  car-videos only) counts every item the section lists, "before" included,
  and so does the section title; "Antes deste vídeo (n)" has its own count.
  `IntakeResponse.message_stored` says a message was kept or changed
  (`ignored` stays true: no job came of it).
* **Job page.** "Mensagens de Maria (3)" is a `<details>`, closed unless one
  of them is a reply to this video; replies are marked "resposta a este
  vídeo", the caption "legenda do vídeo", media "(áudio)", "(imagem)", edits
  "editada", deletions "apagada" (still listed, without text); "Antes deste
  vídeo" is folded inside. The queue row gets "💬 3". If the messages call
  fails, the page says so and works.
* **Retention.** `AdminSettings.sender_messages_keep_days` (Configurações,
  default 14, 1–365 whole days); the background loop deletes older messages
  hourly (`sweepers.delete_old_sender_messages_once`). Group chats stay
  ignored (§19).
* **Deployment.** Orchestrator and review UI only, no worker change (they
  ignore the new answer fields, §14). Run `up.sh` after deploying, so the
  Evolution webhook gets the two new events; until then texts and replies
  are kept but edits and deletions are not seen.

## 31. Leftovers from the 0.3.0 test (LIVESTACK-46, 2026-11-07)

* **Worker page, "Detalhes".** Only Portuguese rows; the status board's
  internal notes ("working folder", "last heartbeat", "accepting new raws",
  "orchestrator") are no longer dumped into the table. The working folder
  is a row of its own, "Pasta de trabalho".
* **"Último erro" says when, and whether it is over.** The board keeps the
  error's time and, after the next answered heartbeat, when it was
  resolved: "HTTP 502 (há 5 min; resolvido)". A start failure is recorded
  there too (it used to go to an English note while "Último erro" said "—").
* **Unknown values from a newer orchestrator.** New *fields* were already
  ignored (§14). New *values* in the fields workers act on (lease status,
  role in the heartbeat answer, job state in state-change answers) are now
  read as plain strings and left alone. Every answer a worker reads goes
  through `read_answer`: an answer it still cannot read is an
  `UnreadableAnswerError` (an `ApiError`), handled by the existing "call
  refused" paths instead of crashing a task. Contract 2.6.1.
* **Installer CI ffmpeg.** The Linux job downloads a pinned static ffmpeg
  build checked against its SHA-256 and cached, instead of `apt-get
  install ffmpeg`, which once hung for 24 minutes on Ubuntu's mirrors.

## 32. Official pass results in the overlay (LIVESTACK-47, 2026-11-07)

The audience wants the result of the passagem as the judges recorded it
(brief "09 — Official pass results and a running clock in the overlay",
part A; part B, the running clock, is LIVESTACK-48). Contract 2.7.0,
installer 0.5.0.

* **The data** (checked against the real 2026 Manobrabilidade in
  `tests/fixtures/results_api/`). `resultado.php?evt=26BR&id=26BR_MAN&display=1`
  answers `columns` plus `rows[].cells[]`, cells in column order (not one
  object per car). A prova with several runs has `list` cells, one value
  per run in run order; raw values are a number only when the text is
  exactly one, so `"66 (4 )"` is text (the icons are gone) and the time is
  read from its leading number (comma or dot). The penalties are only in
  `display`: lines split on `<br />`, each `count<img class='kind'>`,
  struck-through text dropped. If a cell's display lines do not pair one to
  one with its runs, no penalties are shown rather than another run's.
  `"-"`, empty, `"SPOILERS"`, `"?"` and any other text are "no result" and
  never reach the overlay.
* **Configuration**: one JSON text on Configurações, stored as typed
  (`app_settings` key `pass_results_config`), checked on every save
  (`orchestrator/domain/pass_results_config.py`): a bad one answers 422 with
  `details.path` ("provas.MAN.result.decimals") and a `problem` code the
  review UI writes in pt-BR, and the old one stays. Unknown keys are errors
  (a typo must not be ignored silently); keys starting with `_` are
  comments, since JSON has none. Empty = off. Example:

  ```jsonc
  {
    "_comment": "Nacional 2026",                // "_" keys: ignored anywhere
    "penalties": {                              // icon class → its names
      "cone": {"one": "cone", "other": "cones"},
      "gate": {"one": "saída de pista", "other": "saídas de pista"}
    },
    "provas": {
      "MAN": {                                  // the results API's prova id
        "resultado": "MAN",                     // table "<event>_MAN"; default: the key
        "runs": "list_columns",                 // or "rows": one row per run
        "show_by_default": true,                // the job's toggle until a reviewer sets it; default true
        "result": {
          "column": "Tempo",                    // required
          "unit": "s",                          // default "" (no unit)
          "decimals": 0,                        // 0–3, default 0
          "penalties": true,                    // read the icons; default false
          "corrected_column": "Tempo Corrigido" // optional; shown after "→" when it differs
        },
        "clock": {"column": "Tempo", "decimals": 1}  // LIVESTACK-48; validated now
      }
    }
  }
  ```

  A prova needs `result` or `clock`. Ids are 1–3 letters/digits. An icon
  class without a name is shown by its class name ("1 flag") and logged.
  A job's prova is matched by its results API id (`Prova.external_id`, from
  the sync): a manual prova never gets a result line.
* **The text**, from one function (`orchestrator/domain/pass_text.py`) for
  the render task, the job page, "Testar" and the mismatch check: the time
  with its unit, then `·` and the penalties ("8 cones, 2 saídas de pista"),
  then `→` and the corrected value when it differs *as shown* (after
  rounding to the configured decimals). pt-BR numbers: decimal comma,
  half-up rounding, no thousands separator (a time is never read as
  "1.234"). A missing corrected value leaves just the time.
* **Fetching** (`domain/pass_results_fetch.py`, background every 10 s):
  tables of configured provas that have jobs in `awaiting_review`,
  `ready`, `claimed` or `uploaded`, or `delivered`/`done` updated in the
  last 6 h. Each is asked every 30 s, or after its `Cache-Control: max-age`
  when longer: the API says 300 s once the event is finished, so the "is
  the event finished" question needs no `evento.php` call. "Testar" fetches
  at once, reusing an answer from the last 5 s. Table `pass_result_tables`
  (migration 0015) keeps the last good answer and, per table, the last
  attempt, its error, and `failing_since`; a failure never removes the good
  answer, so texts keep coming from it (marked stale) and nothing on air is
  blanked. The Settings page shows that status per configured prova.
* **The job page**: "Mostrar resultado da passagem" next to the passagem
  toggle, only when the prova is configured. `jobs.show_pass_result` is NULL
  until a reviewer sets it, and NULL follows the prova's `show_by_default`
  (`Job.pass_result.show` is the value in force). Under it, the text that
  will be burned in, or why there is none (`PassResultProblem`), and
  "inacessível desde …" when the last fetch failed.
* **The worker** draws `ProcessTask.pass_result_text` as a third pill
  **below** the role pill (its top 3 px under y 940): the name and role
  pills stay exactly where they were, so a clip looks the same with or
  without a result and the existing golden images did not change. Board
  row tokens (`--board-row-height` 40 px, `--size-board` 30 px,
  `--tracking-row`), capitals, the "·" muted like the role pill's. The
  vendored Bebas Neue has **no "→"**: the worker draws the arrow as a
  shape (0.6 em long, centred on the capitals), and a cut text never ends
  on it. Golden images: clean run, cones only, cones and gates, a long
  line that shrinks (`tests/worker/golden/overlay-result-*.png`).
* **Render feature `pass_result`** (in the §29 mechanism; no separate
  gating): a task needs it when it carries a text. A 0.4.0 worker would
  ignore the field and render without the pill, so it is not offered that
  render; the job says "Esperando um PC … 0.5.0" when that is the only
  thing missing. A job without a result (none yet, toggle off, prova not
  configured) needs nothing new and goes to any worker.
* **Results that arrive or change later** (§25): `pass_result_text` is a
  render input. "Now" is the text from the latest fetch, when shown, so a
  result published or corrected after the render, the toggle changed, or
  the prova taken out of the configuration flags the clip ("no longer
  matches", `?outdated=true`) with a sentence saying the result was
  published or changed. A failed fetch never flags anything. Reprocessing
  stays the reviewer's decision.
* **Deployment order: the VPS first, then the workers.** A 0.5.0 worker
  always sends `pass_result_text` (null or not) in its render-inputs
  report, which an orchestrator before 2.7.0 refuses (`extra="forbid"`);
  the worker logs it and delivers, and the version keeps the claim
  snapshot. Heartbeats are fine either way (feature names are plain
  strings). The review UI goes with the orchestrator.
* **Not built / not checked without a real event**: the "rows" mode (run n
  = the car's n-th row) is built as the brief says but no real table of
  that kind was seen, and the API documents rows **in position order**, so
  the n-th row may not be the n-th run; times written as "1:05,3" would be
  "not a number" (none seen); results of delivered clips older than 6 h
  are no longer fetched, so a late correction of those is not flagged.
