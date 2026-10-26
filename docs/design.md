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
  exit); the reviewer sees the message and chooses retry or discard.

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
  `orchestrator/`, `worker/`, `shared/` (schemas, API client, hashing,
  ffmpeg helpers), `infra/` (compose, Caddy, Tailscale ACLs, runbooks),
  `docs/`. The review UI lives inside `orchestrator/` (same process, served
  under `/review`). The old `baja-video-processor` repo is reference only;
  nothing is ported verbatim.
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

