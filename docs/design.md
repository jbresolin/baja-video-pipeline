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

* Raw archive = copy the master worker's folder to TrueNAS, unhurried.

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

