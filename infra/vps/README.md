# Baja staging VPS: the pipeline stack

Runbook for the **video pipeline** on the Baja staging VPS: orchestrator,
review UI and the pipeline's own tailnet listener. The stack runs only
around events: `./up.sh` before, `./down.sh` after.
Design context: [`docs/design.md`](../../docs/design.md) §8, §10, §13,
§15, §20. Orchestrator API: `docs/api.md`.

The VPS runs three stacks (design §20):

| Stack | Repo | Runs | Owns |
| --- | --- | --- | --- |
| **Base** | `jbresolin/baja-vps` | always | Caddy (container `caddy`, host 80/443, imports `/srv/caddy/conf.d/*.caddy`), Evolution (`evolution:8080`), Postgres, Redis, network `baja-edge`, Evolution's backups and its migration runbook, host basics (swap, journald, ufw) |
| Scoring staging | `baja-sae-brasil-online` | always | its nginx on `baja-edge`, behind the base Caddy |
| **Pipeline** | this folder | around events | orchestrator, review UI, `pipeline-edge` (tailnet Caddy), `conf.d/pipeline.caddy` while up, the Evolution instance webhook while up |

Files in this directory:

| File | What it is |
| --- | --- |
| `docker-compose.yml` | orchestrator + review-ui on `baja-edge`, `pipeline-edge` on `${TAILSCALE_IP}:8080` |
| `Caddyfile.tailnet` | `pipeline-edge`'s config: the worker/studio API and inbox/outbox files (forward_auth) |
| `conf.d/pipeline.caddy` | Template of the public routes for the base Caddy; `up.sh` renders and installs it (§7) |
| `up.sh`, `down.sh` (+ `stack_lib.sh`, `stack_ctl.py`) | Bring the stack up / take it down (§6) |
| `.env.example` | Every variable, with comments. Copy to `.env` |
| `compose-smoke.sh` (+ `smoke_checks.py`) | Smoke test, PASS/FAIL table; `up.sh` runs it last (§11) |
| `backup.sh`, `systemd/baja-backup.{service,timer}`, `restore.md` | Nightly backups of the pipeline's state while it is up (§12) |
| `MONITORING.md` | Pointing an external Prometheus/Grafana at `GET /metrics` |
| `EVENT_DAY.md` | Event-day runbook: T-7, T-1, during, after |

```
             internet                                          tailnet (100.x)
                │ 443                                               │ :8080
  ┌─────────────▼──────────────────── VPS ──────────────────────────▼──────────┐
  │  BASE STACK                                │  PIPELINE STACK (this folder) │
  │  caddy ─ evo/scoring sites                 │                               │
  │    └─ import conf.d/pipeline.caddy ────────┼─► {PIPELINE_DOMAIN}           │
  │         /review*         ─► bvp-review-ui:8001                             │
  │         /proxies/* /thumbs/* ─► file_server (/srv/pipeline, read-only)     │
  │         POST /enroll     ─► bvp-orchestrator:8000                          │
  │  evolution ── instance webhook ───────────►  orchestrator:8000            │
  │  postgres, redis                           │   ▲                           │
  │                                            │   │  network "pipeline"       │
  │            ── network baja-edge ──         │  pipeline-edge (Caddy)  ◄─────┘
  │                                            │   /inbox/* /outbox/* forward_auth
  │                                            │   everything else ─► orchestrator
  └────────────────────────────────────────────┴───────────────────────────────┘
```

## Key decisions (why it looks like this)

* **The pipeline owns its tailnet surface.** `pipeline-edge` is a small Caddy
  in this stack, published only on `${TAILSCALE_IP}:8080`. The base Caddy
  carries only public routes, and it carries ours only between `up.sh` and
  `down.sh`.
* **Public routes are a conf.d snippet, rendered at install time.** The base
  Caddy's environment belongs to the base stack, so the snippet cannot read
  `PIPELINE_DOMAIN` from it. `up.sh` substitutes it with `envsubst` (§7).
* **Containers are named for a shared network.** On `baja-edge` the
  orchestrator and review UI answer as `bvp-orchestrator` and `bvp-review-ui`,
  so a generic `orchestrator` from another stack can never shadow them.
* **The webhook follows the stack.** `up.sh` points Evolution's instance
  webhook at the orchestrator; `down.sh` disables it. Between events Evolution
  posts nowhere, and the bot does not answer videos. That is intended.
* **The global Evolution API key stays out of this repo.** `up.sh`, `down.sh`
  and the smoke test use the instance token (`BVP_EVOLUTION_API_KEY`), which
  Evolution accepts for that instance's own routes.

---

## 1. What the base stack must provide

This stack does not start without the base stack. `up.sh` checks the first
three items and stops if one is missing:

| Item | Detail |
| --- | --- |
| Network `baja-edge` | External Docker network; Evolution and the base Caddy are on it, and it has internet egress |
| Container `caddy` | Running, config at `/etc/caddy/Caddyfile` with `import /srv/caddy/conf.d/*.caddy`, admin API on its default `localhost:2019` (for `caddy reload`) |
| `/srv/caddy/conf.d/` | Host folder, bind-mounted into `caddy` at the same path. `up.sh` writes `pipeline.caddy` there |
| `/srv/pipeline` in `caddy` | Read-only bind mount of the host's `/srv/pipeline`, by default at the same path (`CADDY_PIPELINE_DIR` otherwise). The base Caddy serves `proxies/`, `thumbs/` and reads `reviewers.txt` from it. Docker creates an empty root-owned folder if it does not exist yet; §2.1 then fixes the owner |
| Evolution | `evolution:8080` on `baja-edge`, v2.3.x webhook API; reachable from the VPS host at `EVOLUTION_URL` (normally its public hostname) |
| `baja-edge` subnet | `10.201.0.0/24`, pinned by the base stack; `BVP_TRUSTED_PROXY_CIDRS` names it (§9). This compose declares no subnet for it |

## 2. One-time preparation on the VPS

Run all VPS steps as root (`sudo -i`). Host basics (Docker, swap, journald,
the firewall's public ports) are the base stack's runbook.

### 2.1 Checkout and directories

`/srv/pipeline` is `BVP_DATA_DIR` (design §15). The orchestrator mounts it
read-write; `pipeline-edge` and the base Caddy read-only. The orchestrator and
review-UI images run as **uid/gid 10001**, so the data folders and the two
files the orchestrator reads belong to that user (design §17):

```bash
sudo git clone https://github.com/jbresolin/baja-video-pipeline.git /opt/baja
cd /opt/baja/infra/vps
sudo install -d -o 10001 -g 10001 -m 750 /srv/pipeline
sudo install -d -o 10001 -g 10001 /srv/pipeline/{inbox,proxies,thumbs,outbox,db}
# Reviewers (filled in §4) and teams (optional CSV, see below), owned by 10001.
[ -e /srv/pipeline/reviewers.txt ] || sudo install -o 10001 -g 10001 -m 640 /dev/null /srv/pipeline/reviewers.txt
[ -e /srv/pipeline/teams.csv ] || echo 'car_number,team_name,school' | sudo install -o 10001 -g 10001 -m 640 /dev/stdin /srv/pipeline/teams.csv
sudo mkdir -p /var/lib/baja
sudo apt-get install -y gettext-base     # envsubst, for up.sh (usually already there)
```

`teams.csv` (`BVP_TEAMS_FILE`) is one `car_number,team_name,school` line per
car; the orchestrator re-reads it when it changes. It may stay with only
the header: team names then come from `BVP_BAJA_APP_URL`, or stay empty.
Both Caddies run as root inside their containers and read the files read-only.

### 2.2 Binding to the Tailscale IP

`ip_nonlocal_bind` lets Docker publish `pipeline-edge` on the Tailscale IP
even if `tailscaled` has not brought the IP up yet at boot. Without it the
publish fails with "cannot assign requested address":

```bash
printf 'net.ipv4.ip_nonlocal_bind=1\n' | sudo tee /etc/sysctl.d/91-baja-pipeline.conf
sudo sysctl --system
```

### 2.3 Inbox as a 25 GiB filesystem (hard cap, design §10)

The inbox gets its own loop-mounted ext4 file, sized = `BVP_INBOX_CAP_GB`.
When it is full, writes fail with ENOSPC inside the inbox only, and the base
stack on the root filesystem keeps working. The soft cap (`BVP_INBOX_HOLD_GB`,
bot asks senders to hold) is the orchestrator's job, see §10. The image file
lives outside `/srv/pipeline`, so no container can see it.

```bash
sudo fallocate -l 25G /var/lib/baja/inbox.img
sudo mkfs.ext4 -m 0 -L baja-inbox /var/lib/baja/inbox.img
echo '/var/lib/baja/inbox.img /srv/pipeline/inbox ext4 loop,noatime,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount /srv/pipeline/inbox
```

Make Docker wait for that mount at boot. The containers bind-mount
`/srv/pipeline` recursively, and they must see the mounted filesystem, not
the empty directory underneath it:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nRequiresMountsFor=/srv/pipeline/inbox\n' | sudo tee /etc/systemd/system/docker.service.d/10-baja-inbox.conf
sudo systemctl daemon-reload
```

Ownership: the freshly mounted filesystem's root belongs to root, so hand
it back to the containers' user (uid/gid 10001, design §17). Without this
the orchestrator cannot write raws, and `compose-smoke.sh` reports
`owner /srv/pipeline/inbox` as FAIL:

```bash
sudo chown 10001:10001 /srv/pipeline/inbox
```

### 2.4 Firewall

Docker-published ports bypass ufw [R14]. `pipeline-edge` publishes only on
the Tailscale IP, so it is unreachable from the internet either way. If the
base stack's ufw policy does not already allow the tailnet interface, add:

```bash
sudo ufw allow in on tailscale0
```

## 3. `.env`

```bash
sudo cp .env.example .env
sudo chmod 600 .env
openssl rand -hex 24      # run once per secret: BVP_WEBHOOK_SECRET, BVP_ADMIN_TOKEN
sudo nano .env            # fill in everything
sudo docker compose config -q     # must print nothing
```

`BVP_EVOLUTION_INSTANCE` and `BVP_EVOLUTION_API_KEY` (the instance token)
come from the base stack's Evolution; its runbook says where to read them.

## 4. Reviewers file (`/srv/pipeline/reviewers.txt`)

One file of named reviewers, read by the **base Caddy** (basic auth on
`/review*`, `/proxies/*`, `/thumbs/*`, through `conf.d/pipeline.caddy`) and
by the **orchestrator** (`BVP_REVIEWERS_FILE`, `reviewerBasic` in
`docs/api.md`) (design §15).

**Format: one `username bcrypt-hash` pair per line, separated by a space.**
`#` starts a comment line. This is **not** Apache's htpasswd `user:hash`
format (hence the `.txt` name, design §17). Caddy imports the file straight
into its `basic_auth` block, and a colon line fails to load ("username and
password cannot be empty"). Usernames must not contain spaces.

Add a reviewer, then reload the base Caddy (re-running `./up.sh` does the
same, plus its checks):

```bash
HASH=$(sudo docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext 'their-password')
echo "ana $HASH" | sudo tee -a /srv/pipeline/reviewers.txt
sudo chown 10001:10001 /srv/pipeline/reviewers.txt
sudo chmod 640 /srv/pipeline/reviewers.txt
sudo docker exec -w /etc/caddy caddy caddy reload --config /etc/caddy/Caddyfile
```

Remove a reviewer: delete the line, then reload the base Caddy the same way.
The orchestrator needs nothing: it re-reads the file on the next login after
it changes (like `teams.csv`). If an edit leaves the file unparseable it
logs a warning and keeps the previous version; Caddy's reload fails loudly
and keeps running the previous config, so fix the line and reload again.
The file must have **at least one** reviewer before `up.sh`, or the base
Caddy refuses the routes and `up.sh` takes them back out (§6).

## 5. Images on ghcr.io

`.github/workflows/images.yml` pushes `ghcr.io/jbresolin/baja-orchestrator`
and `ghcr.io/jbresolin/baja-review-ui` (tags `main`, the short commit SHA,
`v*`). **GitHub creates container packages private**, so `docker compose
pull` on the VPS fails with `denied` or `unauthorized` until you pick one:

**Option A: log the VPS in with a read-only token** (images stay private).
On GitHub: *Settings → Developer settings → Personal access tokens → Tokens
(classic) → Generate new token*, scope **`read:packages` only**, an
expiry past the event. (ghcr.io has needed a classic token for this; if
GitHub's fine-grained tokens list *Packages: read* for your account by now,
that works too.) Then on the VPS:

```bash
sudo docker login ghcr.io -u jbresolin     # paste the token as the password
```

Docker keeps it in `/root/.docker/config.json` (base64, not encrypted:
that is why the token can only read packages). Renew it before it expires,
and `sudo docker logout ghcr.io` when the VPS is retired.

**Option B: make the two packages public.** On GitHub: your profile →
*Packages* → `baja-orchestrator` → *Package settings* → *Change
visibility* → Public; repeat for `baja-review-ui`. No login on the VPS. The
images hold only the code, which is the same as this repo's: no secrets are
baked in (everything comes from `.env` at run time). Choose this if the
repo itself is public or you don't mind the code being pullable.

## 6. `up.sh` and `down.sh`

```bash
cd /opt/baja/infra/vps
sudo ./up.sh      # before the event (EVENT_DAY.md, T-7)
sudo ./down.sh    # after it (EVENT_DAY.md, "After the event")
```

Both are idempotent: run them again whenever in doubt. Each step checks
first and says what it did or why it did nothing.

**`up.sh`**

1. The base stack is present: network `baja-edge`, container `caddy`
   running, `/srv/caddy/conf.d/` writable. Otherwise it stops before touching
   anything.
2. `docker compose pull` and `docker compose up -d --remove-orphans`.
3. Waits (up to 90 s) for the orchestrator's `/health` through
   `pipeline-edge` on `${TAILSCALE_IP}:8080`.
4. Renders `conf.d/pipeline.caddy` (§7), installs it as
   `/srv/caddy/conf.d/pipeline.caddy` when it differs from what is there, and
   reloads the base Caddy (always: that also picks up reviewer edits). If the
   base Caddy refuses it, the previous file is put back, or the new one
   removed, so a bad file never blocks the other stacks' reloads.
5. Asks Evolution for the instance webhook and sets it (§8) unless it
   already points at `http://bvp-orchestrator:8000/intake/evolution-webhook`
   with the right secret, events, and `base64` off.
6. Runs `compose-smoke.sh` (§11).

Exit 0 = up and every check passed; 1 = up, but a smoke check failed; 2 =
a step failed and `up.sh` stopped there (the message says which).

**`down.sh`**

1. Disables the instance webhook, so Evolution stops posting to an
   orchestrator that is about to stop. A webhook that someone pointed at a
   different URL is left alone.
2. Removes `/srv/caddy/conf.d/pipeline.caddy` and reloads the base Caddy, so
   `PIPELINE_DOMAIN` stops being served (browsers get a TLS error for it
   until the next `up.sh`; fine for a staging box).
3. `docker compose down --remove-orphans`. No `-v`: the stack has no named
   volumes, and `/srv/pipeline` (jobs DB, reviewers, teams, any leftover
   raws) stays as it is for the next `up.sh`.

Every step runs even if an earlier one failed, so the containers always stop.
Exit 0 = all done; 1 = down, but a step needs a look (e.g. Evolution was
unreachable, so the webhook may still be on).

The decisions live in `stack_ctl.py` (stdlib only), tested with fake
`docker`/`curl` in `tests/infra/test_up_down.py`.

## 7. Listeners and routes

**`docs/api.md` is the authority** for the orchestrator's routes, auth
schemes, payloads and data layout (design §15 settles any conflict). Only
the Caddy-side facts live here. If one of them changes, update
`conf.d/pipeline.caddy` or `Caddyfile.tailnet` in the same PR.

| Listener | Reachable from | Served by | Routes |
| --- | --- | --- | --- |
| `:443` `PIPELINE_DOMAIN` | internet | base Caddy, `conf.d/pipeline.caddy` | `/review*` → `bvp-review-ui:8001` (reviewer basic auth); `/proxies/*`, `/thumbs/*` → static (reviewer basic auth); `POST /enroll` → `bvp-orchestrator:8000` (no auth, 16 KB body cap); `/` → redirect to `/review/`; anything else 404 |
| `${TAILSCALE_IP}:8080` | tailnet only | `pipeline-edge`, `Caddyfile.tailnet` | `/inbox/*`, `/outbox/*` → static behind `forward_auth`; everything else → orchestrator |
| `bvp-orchestrator:8000` | `baja-edge` and `pipeline` networks | — | base Caddy (`/enroll`), Evolution's webhook (`POST /intake/evolution-webhook`), `pipeline-edge`, review UI |

**DNS and certificates.** `PIPELINE_DOMAIN` is **DNS-only at Cloudflare
(grey cloud)**: the A/AAAA record points straight at the VPS and Cloudflare
does not proxy it. So the base Caddy gets its certificate with a plain
**HTTP-01** challenge on port 80 (no DNS-provider plugin, no API token), and
the snippet has **no Cloudflare-only matcher** (no `remote_ip` allowlist of
Cloudflare ranges, no `trusted_proxies cloudflare`): the TCP peer the base
Caddy sees is the real client, which is what goes into `X-Forwarded-For`
(§9). Turning the orange cloud on later would break both assumptions: the
`/enroll` rate limit would see Cloudflare's addresses.

**Rendering `conf.d/pipeline.caddy`.** The file in the repo is a template
with two variables, both from `.env`: `${PIPELINE_DOMAIN}` (the site
address) and `${CADDY_PIPELINE_DIR}` (where the base Caddy sees
`/srv/pipeline`: the `file_server` root and the `reviewers.txt` import).
`up.sh` runs

```bash
envsubst '${PIPELINE_DOMAIN} ${CADDY_PIPELINE_DIR}' < conf.d/pipeline.caddy
```

The explicit list matters: only those two are replaced, so Caddy's own
placeholders (`{http.auth.user.id}`) are untouched. Why not Caddy's
`{$PIPELINE_DOMAIN}`: Caddy would resolve that from **the base Caddy
container's** environment, which the base stack defines and this repo does
not. So after changing either value in `.env`, run `./up.sh` again; it
re-renders and reloads. Snippets in the file carry a `bvp_` prefix,
because snippet names are shared with the base Caddyfile and every other
conf.d file.

**forward_auth (`/inbox/*`, `/outbox/*`)**: `pipeline-edge` sends `GET
/auth/file` to `orchestrator:8000` with the client's original headers
(including `Authorization: Bearer …`) plus `X-Forwarded-Method` and
`X-Forwarded-Uri` (the requested path) [R23]. A 2xx lets Caddy serve the
file, and an `X-Worker-Id` response header is copied onto the request for
the access log. Any other status is returned to the worker unchanged.

**Reviewer requests**: the base Caddy checks basic auth against
`reviewers.txt` (format in §4), then proxies `/review*` to `bvp-review-ui:8001`
with the client's `Authorization` header unchanged, plus `X-Reviewer:
<username>`. Caddyfile `basic_auth` caches bcrypt results, so the video
player's many Range requests don't each pay a bcrypt hash.

**Dot-file rule**: both Caddies' `file_server` hide `.*` (plus `*.part`,
`*.part.json`) under the data dir [R24]. Writers create files as dot-files
and rename them when complete, so Caddy never serves half a file. Range
requests (resume, seeking) are handled by `file_server`.

## 8. The Evolution webhook

From the v2.3.7 source [R8] and docs [R13][R20]:

* **Instance webhook**: one per instance, **one URL**, custom `headers`
  supported (so it can carry `X-Webhook-Secret`), its own event list, plus
  `byEvents`/`base64` flags. The orchestrator is its only consumer; the
  global webhook (no custom headers) is the base stack's to keep disabled.
* It is set with **`POST /webhook/set/{instance}`**, body **nested under
  `"webhook"`** with `byEvents`/`base64`. That is what v2.3.7 validates
  [R16]; the docs page shows a flat body [R13], which is wrong for this
  version, and there is no `PUT` route. `url` is required even when
  disabled, so `down.sh` sends ours with `"enabled": false`.
* What `up.sh` sets:
  `{"webhook":{"enabled":true,"url":"http://bvp-orchestrator:8000/intake/evolution-webhook","headers":{"X-Webhook-Secret":"<BVP_WEBHOOK_SECRET>"},"byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","CONNECTION_UPDATE"]}}`.
  The body goes to curl through a temporary file, so the secret is never on
  a command line.
* Evolution posts to `bvp-orchestrator:8000` **over `baja-edge`**. The
  webhook never leaves the host and has no public route.
* Delivery is retried by Evolution (the base stack's `WEBHOOK_RETRY_*`
  settings), which rides out an orchestrator restart.

**Keep `base64` off.** With it on, Evolution downloads *every* incoming
media into a Node Buffer and embeds it in the payload [R22]. A 2 GB WhatsApp
document (design §3) would OOM-kill Evolution. `up.sh` always sends
`base64: false` and treats a webhook with it on as one to fix. The
orchestrator streams and decrypts documents from the WhatsApp CDN itself
(design §15).

**Rotating the webhook secret**: change `BVP_WEBHOOK_SECRET` in `.env`,
then `sudo ./up.sh`. It recreates the orchestrator with the new value and,
seeing the old header on the webhook, sets the new one.

## 9. `/enroll` source IP and `BVP_TRUSTED_PROXY_CIDRS`

`/enroll` is rate-limited per source IP by the orchestrator (10 per 10 min,
`docs/api.md` §2.2). The orchestrator takes the source IP from the **last
`X-Forwarded-For` entry**, but only when the TCP peer is inside
`BVP_TRUSTED_PROXY_CIDRS`. Otherwise it uses the peer itself.

For a public `/enroll`, the peer is **the base Caddy's address on
`baja-edge`**, because that is the network it uses to reach
`bvp-orchestrator`. The base Caddy sets `X-Forwarded-For` to the client's
address (the TCP peer it sees: `PIPELINE_DOMAIN` is DNS-only, so that is the
real client, §7) and drops any value the client sent.

| Network | Subnet | Set by | Who comes from it |
| --- | --- | --- | --- |
| `baja-edge` | `10.201.0.0/24` | the base stack (this compose joins it as `external`, with no subnet declaration) | the base Caddy (public `/enroll`), Evolution (webhook) |
| `pipeline` | `10.202.0.0/24` | `docker-compose.yml` here (pinned so it can be named) | `pipeline-edge` (tailnet requests) |

So `.env.example` and `docker-compose.yml` set

```
BVP_TRUSTED_PROXY_CIDRS=127.0.0.1/32,10.201.0.0/24,10.202.0.0/24
```

and the orchestrator's own default (no variable at all) is
`127.0.0.1/32,10.201.0.0/24,172.16.0.0/12`. Only containers on those two
networks could spoof `X-Forwarded-For`: the base and scoring stacks and this
one.

* If `baja-edge` is not covered, every volunteer's enrollment counts against
  the base Caddy's single IP, and the 11th PC in 10 minutes gets `429`.
  `compose-smoke.sh` reads the subnets with
  `docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' baja-edge`
  and reports `trusted proxies cover baja-edge` as FAIL if one is not covered,
  so a change of subnet in the base stack shows up at the next `up.sh`.
* `.env` wins over `orchestrator.env` for this variable (`docker-compose.yml`
  passes it in `environment:`).

## 10. Memory, disk, and where the caps are enforced

### 10.1 Memory (4 GB box, shared with the base stack)

| Container | Limit | Notes |
| --- | --- | --- |
| orchestrator | 768 MiB | FastAPI + SQLite. Streams downloads/uploads to disk, never holds a file in RAM |
| review-ui | 160 MiB | |
| pipeline-edge | 128 MiB | plain HTTP + static files |
| **Pipeline total** | **1056 MiB** | only while up; the base stack's budget is in its own runbook |

`docker stats --no-stream` shows actual use.

### 10.2 Disk (70 GB) and caps (design §10, §15)

Throughput assumption: 200 passes × 500 MB = 100 GB/day through the VPS,
pulled twice = 200 GB egress (about 4.5 h at 100 Mbps, spread over the day).
The VPS is **transit only**, so this volume never sits on disk at once.

| Consumer | Budget | Enforced by |
| --- | --- | --- |
| Pipeline images | ~1 GB | `docker image prune` after an event |
| Container logs | ≤ 30 MB per container | json-file rotation (`x-logging` in compose) |
| **Inbox** `/srv/pipeline/inbox` (raws waiting for a 2nd holder) | **hard cap 25 GiB**, normally near-empty | **Kernel:** 25 GiB loop filesystem (§2.3), the same size as `BVP_INBOX_CAP_GB=25`, which the orchestrator also enforces. **Soft cap: orchestrator** at `BVP_INBOX_HOLD_GB=22`, where the bot tells senders to hold. Only fills if all workers are offline. |
| **Proxies + thumbs** | **~6 GB per 200-pass event** | **Orchestrator:** evicts the oldest *completed* jobs first when root free space < `BVP_PROXY_EVICT_MIN_FREE_GB=10`. Any holder regenerates on request. |
| **Outbox** | fallback only | **Orchestrator:** deleted after the streaming PC's ACK |
| Jobs DB `/srv/pipeline/db/` | small (SQLite WAL) | — |
| Backups `/var/backups/baja/` | a few MB each, 14 kept | `backup.sh` retention |
| Free headroom | ≥ 10 GB | same threshold as proxy eviction |

Watch: `df -h / /srv/pipeline/inbox` and `sudo du -sh /srv/pipeline/*`.

## 11. Smoke test

`up.sh` runs it last. By hand:

```bash
cd /opt/baja/infra/vps
sudo ./compose-smoke.sh
```

Read-only. It prints one row per check and ends with `ALL CHECKS PASSED`
(exit 0) or `N CHECK(S) FAILED` (exit 1); exit 2 means it could not read
`.env`. What it checks:

| Check | Passes when | If it fails |
| --- | --- | --- |
| `base network baja-edge` | the network exists | start the base stack |
| `trusted proxies cover baja-edge` | every subnet of it is inside `BVP_TRUSTED_PROXY_CIDRS` | §9 |
| `base container caddy` | running | the base stack's logs |
| `caddy routes installed` | `/srv/caddy/conf.d/pipeline.caddy` exists, for `PIPELINE_DOMAIN` | `./up.sh` |
| `container <service>` | orchestrator, review-ui, pipeline-edge `running`, and `healthy` where they have a healthcheck (pipeline-edge has none) | `docker compose logs <service>` |
| `caddy 443 <PIPELINE_DOMAIN>` | `/review/` answers 401 (basic auth) over TLS | DNS, the base Caddy's certificate and logs |
| `orchestrator /health via <TAILSCALE_IP>:8080` | 200 `{"status":"ok"}` | `ip_nonlocal_bind` (§2.2), `tailscale ip -4` = `TAILSCALE_IP`, `docker compose logs pipeline-edge` |
| `orchestrator via 443 (POST /enroll)` | an empty body comes back 400 from the orchestrator | orchestrator down or not on `baja-edge`. The public site has no `/health` (design §8), so this is the 443 probe. It uses one of the VPS IP's 10 `/enroll` tries per 10 min |
| `orchestrator /metrics` | 200 with the admin token, `bvp_jobs` present | `BVP_ADMIN_TOKEN` |
| `evolution instance state` | `open` | the base stack's runbook. **Do not scan a QR** as a first reaction |
| `evolution webhook` | enabled, pointing at `bvp-orchestrator` | `./up.sh` (§8) |
| `reviewers file` | parses with the orchestrator's rules and has at least one reviewer | §4 (`user hash`, one space, no colon) |
| `owner <path>` | `/srv/pipeline`, its five folders, `reviewers.txt` and `teams.csv` are `10001:10001` | the `chown` the row prints |
| `inbox filesystem` | `/srv/pipeline/inbox` is its own mount, no bigger than `BVP_INBOX_CAP_GB` GiB | §2.3 |

The decisions live in `smoke_checks.py` (stdlib only, plus the repo's
`shared/reviewers_file.py`), tested in `tests/infra/test_compose_smoke.py`
with fake `docker`/`curl`/`findmnt`.

## 12. Backups and restore

`backup.sh` runs nightly from a systemd timer and writes one dated folder
to `/var/backups/baja/`, keeping the last 14: the jobs DB (SQLite online
backup), `reviewers.txt`, `teams.csv`. Evolution's data is the base stack's
to back up.

The unit's `ExecCondition` runs `backup.sh --pipeline-present`, which asks
Docker for this stack's orchestrator container (running or stopped). After
`down.sh` removed it, the nightly run is skipped (not failed), so nights
between events add no copies of an idle DB. Install once:

```bash
sudo install -m 644 /opt/baja/infra/vps/systemd/baja-backup.service /opt/baja/infra/vps/systemd/baja-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baja-backup.timer
sudo systemctl start baja-backup.service      # one run now, to check (needs the stack up)
sudo journalctl -u baja-backup.service -n 30
ls -l /var/backups/baja/
```

What is in each backup, and how to bring it back: [`restore.md`](restore.md).

## 13. Moving from the old single compose

Before this layout, one `docker-compose.yml` here (project `baja`) ran
Evolution, Postgres, Redis, Caddy and the pipeline together. If a VPS
still runs it, the base stack's runbook moves Evolution and its volumes
(`baja_postgres_data`, `baja_redis_data`, `baja_evolution_instances`) and
takes over 80/443. For the pipeline part, after that:

```bash
sudo docker rm -f baja-orchestrator-1 baja-review-ui-1      # old project's pipeline containers, if still there
cd /opt/baja && sudo git pull && cd infra/vps
# .env: drop the Evolution/Postgres/Manager variables, add EVOLUTION_URL and
# CADDY_PIPELINE_DIR (compare with .env.example)
sudo ./up.sh
```

`/srv/pipeline` is reused as it is. The old `baja_caddy_data` volume only
held certificates; the base Caddy gets its own.

## 14. References

Evolution API (official docs live at docs.evolutionfoundation.com.br; doc.evolution-api.com redirects there):

* [R8] Webhook delivery (instance vs global, retries, headers only on instance): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.controller.ts
* [R13] Find webhook / set webhook (docs): https://docs.evolutionfoundation.com.br/evolution-api/get-webhook.md , https://docs.evolutionfoundation.com.br/evolution-api/set-webhook.md
* [R16] Set-webhook body schema actually validated in v2.3.7: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.schema.ts
* [R20] Webhooks overview (instance vs global): https://docs.evolutionfoundation.com.br/evolution-api/configuration/webhooks.md
* [R22] Media buffered when `webhookBase64` is on (`messages.upsert`): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts

Other:

* [R14] Docker and ufw: https://docs.docker.com/engine/network/packet-filtering-firewalls/
* [R23] Caddy `forward_auth`: https://caddyserver.com/docs/caddyfile/directives/forward_auth
* [R24] Caddy `file_server` (`hide`, Range → 206): https://caddyserver.com/docs/caddyfile/directives/file_server ; `basic_auth` (hash cache enabled from the Caddyfile: https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/caddyauth/caddyfile.go): https://caddyserver.com/docs/caddyfile/directives/basic_auth ; `import`: https://caddyserver.com/docs/caddyfile/directives/import
* GitHub container registry, visibility and authentication: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
