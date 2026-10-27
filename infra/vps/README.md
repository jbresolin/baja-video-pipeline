# Baja staging VPS: Evolution + Caddy (LIVESTACK-30)

Copy-paste runbook for moving the **Baja Bot** Evolution API instance from
the TrueNAS box at home to the Baja staging VPS **without re-scanning the QR
code**, and for running it next to the pipeline orchestrator behind Caddy.
Design context: [`docs/design.md`](../../docs/design.md) §2, §3, §8, §10,
§13, §15. Orchestrator API: `docs/api.md`.

Files in this directory:

| File | What it is |
| --- | --- |
| `docker-compose.yml` | Evolution API v2.3.7 + Postgres + Redis, Caddy, orchestrator (`BVP_*`) |
| `Caddyfile` | Public Evolution vhost, public pipeline vhost (review/proxies/thumbs/enroll), tailnet-only listener |
| `.env.example` | Every variable, with comments. Copy to `.env` |

```
                 internet                                    tailnet (100.x)
                    │ 443                                         │ :8080
              ┌─────▼──────────────────── VPS ────────────────────▼─────┐
              │  caddy ── {EVO_DOMAIN} ──────────► evolution:8080        │
              │    │      (apikey hdr; /manager = basic auth)  │         │
              │    │                                           │ net     │
              │    ├──── {PIPELINE_DOMAIN}                     │ evolution_db
              │    │       /review*            ─► orchestrator ├─ postgres
              │    │       /proxies/* /thumbs/* ─► file_server └─ redis
              │    │       POST /enroll (no auth) ─► orchestrator        │
              │    └──── http://:8080 (TAILSCALE_IP only)                │
              │            /inbox/* /outbox/* ─► forward_auth + file_server
              │            everything else    ─► orchestrator:8000       │
              │  evolution ──instance webhook──► orchestrator:8000       │
              │              POST /intake/evolution-webhook (internal)   │
              └──────────────────────────────────────────────────────────┘
```

## Key decisions (why it looks like this)

* **Image `evoapicloud/evolution-api:v2.3.7`.** That is the latest stable
  release (GitHub "Latest", Docker Hub tag pushed 2025-12-05). `latest` on
  Docker Hub was re-pushed on 2026-05-06, the same day as `2.4.0-rc1`, so
  it is not a safe pin. 2.4.0 is still RC and adds mandatory license
  activation (§10). Images moved from `atendai/` to `evoapicloud/` at
  v2.3.0. [R1][R2][R3]
* **The WhatsApp session is spread over three stores.** A plain volume copy
  is not enough. With Postgres + Redis enabled (Evolution's default), the
  Baileys auth state is split up [R4][R5]:
  * `creds` (identity keys, noise key, registration) are in the Postgres table
    `"Session"` (`sessionId` = instance id).
  * Every other Signal key (pre-keys, sessions, sender keys, app-state sync
    keys) is in a Redis hash `<CACHE_REDIS_PREFIX_KEY>:instance:<instanceId>`
    with **no TTL**. If Redis is disabled, the same keys are JSON files in
    `/evolution/instances/<instanceId>/` instead.
  * Instances are loaded only when their `clientName` equals
    `DATABASE_CONNECTION_CLIENT_NAME` [R6].

  So the runbook copies **all three** (Postgres dump, Redis snapshot,
  instances volume) and keeps the prefix, DB index, schema and client name
  identical. Evolution's docs have no migration page (checked the full
  index [R7]), so these claims cite the v2.3.7 source.
* **One webhook consumer: the orchestrator, via the instance webhook.** It
  can carry the `X-Webhook-Secret` header. The global webhook is disabled
  (design §15). Details in §6.
* **Caddy is the only container with published ports.** Everything else
  talks over Docker networks. Postgres/Redis are on an `internal` network
  that only Evolution can reach.

---

## 0. What you need before starting

* VPS: Ubuntu, Docker Engine + Compose plugin **≥ 2.24** (`docker compose
  version`; the optional `env_file` entry needs it), root/sudo, SSH.
* The VPS has joined the Baja tailnet as `tag:server` (LIVESTACK-29). Note
  its IP: `tailscale ip -4`.
* Two DNS names pointing at the VPS public IP: `EVO_DOMAIN` (e.g.
  `evo.<domain>`) and `PIPELINE_DOMAIN` (e.g. `review.<domain>`).
* SSH access to TrueNAS (shell with `sudo docker`).
* A maintenance window of about 30 minutes. During it the bot neither
  receives nor answers. WhatsApp queues incoming messages and delivers them
  on reconnect.

> **Golden rule:** the old and the new Evolution must **never run at the same
> time** with the same session, and you must **never call logout or delete**
> on the instance (on either side). Logout unlinks the device in WhatsApp,
> and then a QR scan is unavoidable [R9].

---

## 1. Pre-flight on TrueNAS (read-only, do it days before)

### 1.1 Find the containers and their storage

```bash
sudo docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep -Ei 'evolution|postgres|redis'
```

Set these for the rest of the session (use your actual names):

```bash
EVO_C=evolution_api        # Evolution API container
PG_C=evolution_postgres    # its Postgres
REDIS_C=evolution_redis    # its Redis
```

Write down mounts and the restart policy (needed for rollback):

```bash
for c in "$EVO_C" "$PG_C" "$REDIS_C"; do echo "== $c"; sudo docker inspect "$c" --format 'restart={{.HostConfig.RestartPolicy.Name}}{{println}}{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{println}}{{end}}'; done
```

Evolution's instance data is mounted at `/evolution/instances` [R10].
Check whether Postgres/Redis are **shared with other apps**. The procedure
below never stops them and only copies Evolution's data, so sharing is
fine. Just know it.

### 1.2 Record the values that must match on the VPS

The process environment overrides the defaults baked into the image at
`/evolution/.env`. Read both:

```bash
sudo docker inspect "$EVO_C" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(SERVER_URL|AUTHENTICATION_API_KEY|DATABASE_|CACHE_|PROVIDER_|CONFIG_SESSION_)' | sort
sudo docker exec "$EVO_C" sh -c 'grep -E "^(DATABASE_CONNECTION_CLIENT_NAME|CACHE_REDIS_PREFIX_KEY|CACHE_REDIS_URI|CACHE_REDIS_SAVE_INSTANCES|PROVIDER_ENABLED)=" /evolution/.env'
```

Copy into the VPS `.env` (see `.env.example`, marked `[MATCH TRUENAS]`):

| TrueNAS variable | VPS `.env` |
| --- | --- |
| `AUTHENTICATION_API_KEY` | `EVOLUTION_API_KEY` |
| `DATABASE_CONNECTION_CLIENT_NAME` | `EVOLUTION_CLIENT_NAME` |
| `?schema=` of `DATABASE_CONNECTION_URI` | `EVOLUTION_DB_SCHEMA` |
| `/N` at the end of `CACHE_REDIS_URI` | `EVOLUTION_REDIS_DB` |
| `CACHE_REDIS_PREFIX_KEY` (code default `evolution-cache` [R11]) | `EVOLUTION_REDIS_PREFIX` |
| `CACHE_REDIS_SAVE_INSTANCES` | `EVOLUTION_REDIS_SAVE_INSTANCES` |
| `CONFIG_SESSION_PHONE_CLIENT` / `_NAME` | `EVOLUTION_PHONE_CLIENT` / `_NAME` |

Also note the user/password/db name inside `DATABASE_CONNECTION_URI`
(needed for `pg_dump`):

```bash
PGU=<user from URI>; PGD=<db from URI>; SCHEMA=<schema from URI>
EVO_KEY=<AUTHENTICATION_API_KEY>; EVO_NAME=<instance name>
```

**Stop conditions.** These setups are not covered here. Stop and plan
separately if any applies:
`DATABASE_PROVIDER` is not `postgresql`, `PROVIDER_ENABLED=true`, or the
Evolution version (next step) is 1.x.

### 1.3 Versions

```bash
sudo docker exec "$EVO_C" wget -qO- http://127.0.0.1:8080/ ; echo
sudo docker exec "$PG_C" postgres --version
sudo docker exec "$REDIS_C" redis-server --version
```

* Evolution **2.x older than 2.3.7**: fine. The new container runs Prisma
  migrations on start (`Docker/scripts/deploy_database.sh` in the image
  entrypoint [R12]). The TrueNAS DB is only *dumped*, never migrated, so
  rollback stays possible.
* Postgres: the VPS major (`postgres:15.19-alpine`) must be **≥** TrueNAS's.
  If TrueNAS is on 16/17, change the image in `docker-compose.yml` first.
* Redis: the VPS (`redis:8.10.2-alpine`) must be **≥** TrueNAS's, so it can
  load the snapshot.

### 1.4 Record the instance and the key counts

```bash
sudo docker exec "$PG_C" psql -U "$PGU" -d "$PGD" -c "SELECT id, name, \"connectionStatus\", \"clientName\", token FROM \"$SCHEMA\".\"Instance\";"
sudo docker exec "$PG_C" psql -U "$PGU" -d "$PGD" -c "SELECT \"sessionId\", length(creds) FROM \"$SCHEMA\".\"Session\";"
sudo docker exec "$EVO_C" wget -qO- --header "apikey: $EVO_KEY" "http://127.0.0.1:8080/webhook/find/$EVO_NAME" ; echo
```

* `id` = the instance id (`INSTANCE_ID=<id>`). `name` → `BVP_EVOLUTION_INSTANCE`.
  `token` → `BVP_EVOLUTION_API_KEY` in the VPS `.env` (the orchestrator
  still accepts the older name `BVP_EVOLUTION_INSTANCE_TOKEN`).
* The webhook JSON [R13] is the old consumer's config. It comes over with
  Postgres and gets replaced in §3.8/§6.2. Just keep a copy.

Count the Redis keys for this instance (compare after the restore):

```bash
REDIS_DB=<N>; REDIS_PREFIX=<prefix>
sudo docker exec "$REDIS_C" redis-cli -n "$REDIS_DB" --scan --pattern "*$INSTANCE_ID*" | wc -l
sudo docker exec "$REDIS_C" redis-cli -n "$REDIS_DB" HLEN "$REDIS_PREFIX:instance:$INSTANCE_ID"
```

(Add `-a <password>` to `redis-cli` if TrueNAS's Redis has one.) With
`CACHE_REDIS_ENABLED=true` and `SAVE_INSTANCES=false`, `HLEN` should be
in the hundreds. Zero means the keys are in files under
`/evolution/instances/<id>/` (Redis disabled), and those are copied too.

---

## 2. Prepare the VPS (any time before the cutover)

Run all VPS steps as root (`sudo -i`). `.env` is mode 600, and later steps
load it into the shell with `set -a; . ./.env; set +a`.

### 2.1 Checkout and directories

`/srv/pipeline` is `BVP_DATA_DIR` (design §15). The orchestrator mounts it
read-write and Caddy read-only. The orchestrator and review-UI images run
as **uid/gid 10001**, so the data folders and the two files the
orchestrator reads belong to that user (design §17):

```bash
sudo git clone https://github.com/jbresolin/baja-video-pipeline.git /opt/baja
cd /opt/baja/infra/vps
sudo install -d -o 10001 -g 10001 -m 750 /srv/pipeline
sudo install -d -o 10001 -g 10001 /srv/pipeline/{inbox,proxies,thumbs,outbox,db}
# Reviewers (filled in §2.6) and teams (optional CSV, see below), owned by 10001.
[ -e /srv/pipeline/reviewers.txt ] || sudo install -o 10001 -g 10001 -m 640 /dev/null /srv/pipeline/reviewers.txt
[ -e /srv/pipeline/teams.csv ] || echo 'car_number,team_name,school' | sudo install -o 10001 -g 10001 -m 640 /dev/stdin /srv/pipeline/teams.csv
sudo mkdir -p /var/lib/baja /root/evo-migration
```

`teams.csv` (`BVP_TEAMS_FILE`) is one `car_number,team_name,school` line per
car; the orchestrator re-reads it when it changes. It may stay with only
the header: team names then come from `BVP_BAJA_APP_URL`, or stay empty.
Caddy runs as root inside its container and reads both files read-only.

### 2.2 Kernel settings, swap, journald

`ip_nonlocal_bind` lets Docker bind Caddy's tailnet port to the Tailscale IP
even if `tailscaled` has not brought the IP up yet at boot. Without it the
publish fails with "cannot assign requested address".

```bash
printf 'vm.swappiness=10\nnet.ipv4.ip_nonlocal_bind=1\n' | sudo tee /etc/sysctl.d/90-baja.conf
sudo sysctl --system
```

**Swap: 2 GiB recommended on the 4 GB box.** The containers can't use it
(`memswap_limit = mem_limit`, so a leaking container is OOM-killed and
restarted, not slowly swapped). It absorbs host-side spikes (apt,
dockerd, image pulls) so they don't trigger the OOM killer on Evolution.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Cap the system journal:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' | sudo tee /etc/systemd/journald.conf.d/90-baja.conf
sudo systemctl restart systemd-journald
```

### 2.3 Inbox as a 25 GiB filesystem (hard cap, design §10)

The inbox gets its own loop-mounted ext4 file, sized = `BVP_INBOX_CAP_GB`.
When it is full, writes fail with ENOSPC inside the inbox only, and
Postgres/Redis/Evolution on the root filesystem keep working. The soft cap
(`BVP_INBOX_HOLD_GB`, bot asks senders to hold) is the orchestrator's job,
see §8. The image file lives outside `/srv/pipeline`, so no container can
see it.

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

Ownership: the orchestrator PR states its container UID. Then
`sudo chown -R <uid>:<uid> /srv/pipeline`.

### 2.4 Firewall

Docker-published ports bypass ufw [R14]. That's acceptable here, because
only Caddy publishes (80/443 public, 8080 on the Tailscale IP only). Still
close everything else on the host:

```bash
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw allow in on tailscale0
sudo ufw enable
```

Also allow 80/443 in the VPS provider's firewall, if it has one.

### 2.5 `.env`

```bash
sudo cp .env.example .env
sudo chmod 600 .env
openssl rand -hex 24      # run once per secret: POSTGRES_PASSWORD, BVP_WEBHOOK_SECRET, BVP_ADMIN_TOKEN
sudo docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext 'manager-password'
sudo nano .env            # fill in everything; put the bcrypt hash in SINGLE quotes
```

Then validate. This must print nothing:

```bash
sudo docker compose config -q
```

### 2.6 Reviewers file (`/srv/pipeline/reviewers.txt`)

One file of named reviewers, read by **Caddy** (basic auth on `/review*`,
`/proxies/*`, `/thumbs/*`) and by the **orchestrator** (`BVP_REVIEWERS_FILE`,
`reviewerBasic` in `docs/api.md`) (design §15).

**Format: one `username bcrypt-hash` pair per line, separated by a space.**
`#` starts a comment line. This is **not** Apache's htpasswd `user:hash`
format (hence the `.txt` name, design §17). Caddy imports the file straight into its `basic_auth` block,
and a colon line fails to load ("username and password cannot be empty").
Usernames must not contain spaces.

Add a reviewer:

```bash
HASH=$(sudo docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext 'their-password')
echo "ana $HASH" | sudo tee -a /srv/pipeline/reviewers.txt
sudo chown 10001:10001 /srv/pipeline/reviewers.txt
sudo chmod 640 /srv/pipeline/reviewers.txt
sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Remove a reviewer: delete the line, then reload the same way. The
orchestrator also has to pick up the change, so follow its PR (re-read or
`docker compose restart orchestrator`). The file must exist with **at
least one** reviewer before Caddy starts, or Caddy refuses the config.

### 2.7 Pull images, create volumes, start Caddy early

```bash
sudo docker compose pull
sudo docker compose create postgres redis evolution
sudo docker compose up -d caddy
sudo docker compose logs caddy | grep -i -E 'certificate obtained|error'
```

Caddy gets the TLS certificates now, so the cutover doesn't wait on ACME.
Until Evolution runs, `https://$EVO_DOMAIN/` with an `apikey` header
returns 502, which is expected.

---

## 3. Cutover: stop old → copy → start new

Expected downtime: about 15 minutes. Run the TrueNAS part (3.1–3.3) and the
VPS part (3.4–3.8) back to back.

### 3.1 TrueNAS: stop the old Evolution for good

```bash
sudo docker update --restart=no "$EVO_C"
sudo docker stop "$EVO_C"
sudo docker ps --filter "name=$EVO_C"      # must list nothing
```

If Evolution is a TrueNAS **App**, also stop the app in the UI, so the
middleware doesn't restart it. If that stops its Postgres/Redis too,
start just those two again: `sudo docker start "$PG_C" "$REDIS_C"`. Only
the API container must stay down.

### 3.2 TrueNAS: export the three stores

```bash
mkdir -p ~/evo-migration
cd ~/evo-migration
# (a) Postgres: logical dump of Evolution's DB (Session, Instance, Webhook, ... tables)
sudo docker exec "$PG_C" pg_dump -U "$PGU" -d "$PGD" -Fc --no-owner --no-privileges -f /tmp/evolution.dump
sudo docker cp "$PG_C":/tmp/evolution.dump ./evolution.dump
# (b) Redis: consistent RDB snapshot over the replication protocol (doesn't stop Redis)
sudo docker exec "$REDIS_C" redis-cli --rdb /tmp/evolution.rdb
sudo docker cp "$REDIS_C":/tmp/evolution.rdb ./evolution.rdb
# (c) Instances volume (works on a stopped container)
sudo docker cp "$EVO_C":/evolution/instances ./instances
sudo tar -C ./instances -czf evolution_instances.tgz .
sha256sum evolution.dump evolution.rdb evolution_instances.tgz | tee SHA256SUMS
```

These files contain the WhatsApp private keys. Treat them like a password.

### 3.3 Transfer to the VPS

```bash
scp evolution.dump evolution.rdb evolution_instances.tgz SHA256SUMS root@<vps>:/root/evo-migration/
```

### 3.4 VPS: verify and restore Postgres

```bash
cd /root/evo-migration
sha256sum -c SHA256SUMS
cd /opt/baja/infra/vps
set -a; . ./.env; set +a
INSTANCE_ID=<id from §1.4>
sudo docker compose up -d postgres
sudo docker compose cp /root/evo-migration/evolution.dump postgres:/tmp/evolution.dump
sudo docker compose exec postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges /tmp/evolution.dump'
sudo docker compose exec postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c 'SELECT id, name, \"clientName\" FROM \"$EVOLUTION_DB_SCHEMA\".\"Instance\";'"
```

Keep this shell open for §3.5–§6, because later steps use the loaded
variables. The row must show the same `id` and `clientName` as in §1.4.
If `pg_restore` only complains that `schema "public" already exists`, that
is harmless.

### 3.5 VPS: restore Redis (RDB → AOF the safe way)

Our Redis runs with `appendonly yes`. If Redis starts with AOF enabled and
no AOF files present, it ignores `dump.rdb`. So load the RDB with AOF
off, then switch AOF on *live*. That's the procedure Redis documents [R15]:

```bash
sudo docker run --rm -v baja_redis_data:/data -v /root/evo-migration:/backup alpine:3.22 cp /backup/evolution.rdb /data/dump.rdb
sudo docker run -d --name redis-import -v baja_redis_data:/data redis:8.10.2-alpine redis-server --appendonly no
sudo docker exec redis-import redis-cli -n "$EVOLUTION_REDIS_DB" HLEN "$EVOLUTION_REDIS_PREFIX:instance:$INSTANCE_ID"
sudo docker exec redis-import redis-cli CONFIG SET appendonly yes
sudo docker exec redis-import redis-cli INFO persistence | grep -E 'aof_rewrite_in_progress|aof_rewrite_scheduled|aof_last_bgrewrite_status'
```

Repeat the last command until both `*_in_progress` and `*_scheduled` are
`0` and the status is `ok`. Then:

```bash
sudo docker exec redis-import redis-cli SHUTDOWN
sudo docker rm redis-import
sudo docker compose up -d redis
sudo docker compose exec redis redis-cli -n "$EVOLUTION_REDIS_DB" HLEN "$EVOLUTION_REDIS_PREFIX:instance:$INSTANCE_ID"
```

The `HLEN` must equal the TrueNAS number from §1.4.

### 3.6 VPS: restore the instances volume

```bash
sudo docker run --rm -v baja_evolution_instances:/evolution/instances -v /root/evo-migration:/backup alpine:3.22 tar -C /evolution/instances -xzf /backup/evolution_instances.tgz
sudo docker run --rm -v baja_evolution_instances:/evolution/instances alpine:3.22 ls -la /evolution/instances
```

### 3.7 VPS: start Evolution

```bash
sudo docker compose up -d evolution
sudo docker compose logs -f evolution
```

Expect the Prisma migrations (if TrueNAS was older), then the instance
loading. **Do not open the Manager and do not click "connect"/QR.**

### 3.8 VPS: retire the old webhook

The instance webhook came over in Postgres, still pointing at the old
consumer. Nothing listens there any more, and Evolution would retry every
event against it. If the orchestrator is **already running**, skip this
and do §6.2 now. Otherwise disable the webhook until it is:

```bash
curl -sS -X POST "https://$EVO_DOMAIN/webhook/set/$BVP_EVOLUTION_INSTANCE" \
  -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
  -d '{"webhook":{"enabled":false,"url":"http://orchestrator:8000/intake/evolution-webhook","byEvents":false,"base64":false,"events":[]}}'
```

The body is **nested under `"webhook"`** with `byEvents`/`base64`. That's
what v2.3.7 validates [R16]. The docs page shows a flat body [R13], which is
wrong for this version. `url` is required even when disabled.

---

## 4. Verify: connected without a QR

```bash
curl -sS -H "apikey: $EVOLUTION_API_KEY" "https://$EVO_DOMAIN/instance/connectionState/$BVP_EVOLUTION_INSTANCE" ; echo
```

Must be `{"instance":{"instanceName":"…","state":"open"}}` [R17]. Then:

1. Phone of the Baja Bot number → *Linked devices*. The **same** device entry
   as before ("Evolution API"/Chrome), with recent activity and **no new
   device**.
2. Send a test message from the bot [R18]:
   ```bash
   curl -sS -X POST "https://$EVO_DOMAIN/message/sendText/$BVP_EVOLUTION_INSTANCE" \
     -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
     -d '{"number":"55XXXXXXXXXXX","text":"VPS migration test"}'
   ```
3. Reply to the bot from that phone, then check that the reply was received
   **and decrypted**. It is stored, because `DATABASE_SAVE_DATA_NEW_MESSAGE=true`:
   ```bash
   sudo docker compose exec postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c 'SELECT \"messageType\", \"pushName\", to_timestamp(\"messageTimestamp\") FROM \"$EVOLUTION_DB_SCHEMA\".\"Message\" ORDER BY \"messageTimestamp\" DESC LIMIT 3;'"
   ```
   Your reply should be the top row with a real type (`conversation`,
   `extendedTextMessage`), and Evolution's logs should show no `Bad MAC`.

**If the state is `close`/`connecting`, or a QR appears:**

* `fetchInstances` [R19] is empty → `EVOLUTION_CLIENT_NAME` or
  `EVOLUTION_DB_SCHEMA` doesn't match. Fix `.env`, `docker compose up -d evolution`.
* The instance exists but the keys are missing → `EVOLUTION_REDIS_DB`/`_PREFIX`
  mismatch, or §3.5 skipped. Fix it and restart.
* Make sure the TrueNAS container is really down (§3.1). Two live copies
  conflict.
* After 15 minutes without progress → rollback (§9). Scanning a new QR is
  the last resort, not a fix. It also works with the same number, but it
  creates a new linked device.

---

## 5. Public checks

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "https://$EVO_DOMAIN/"                 # 401 (no apikey header)
curl -sS -o /dev/null -w '%{http_code}\n' "https://$EVO_DOMAIN/manager"          # 401 (basic auth)
curl -sS -o /dev/null -w '%{http_code}\n' -H "apikey: $EVOLUTION_API_KEY" "https://$EVO_DOMAIN/"   # 200
curl -sS -o /dev/null -w '%{http_code}\n' "https://$PIPELINE_DOMAIN/review/"     # 401
curl -sS -o /dev/null -w '%{http_code}\n' "https://$PIPELINE_DOMAIN/thumbs/x.jpg" # 401
curl -sS -o /dev/null -w '%{http_code}\n' "https://$PIPELINE_DOMAIN/enroll"      # 404 (GET is not routed)
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' "http://<vps-public-ip>:8080/"    # must time out / refuse
```

Manager UI: `https://$EVO_DOMAIN/manager`. First the basic auth, then
Evolution's own login with the API key. Once the orchestrator runs,
`POST https://$PIPELINE_DOMAIN/enroll` reaches it without auth (a bad
token → 401 `enrollment_token_invalid`).

---

## 6. Webhook → orchestrator

### 6.1 How Evolution v2 delivers

From the v2.3.7 source [R8] and docs [R13][R20]:

* **Instance webhook**: one per instance (`"Webhook".instanceId` is
  unique), **one URL**, custom `headers` supported, own event list, plus
  `byEvents`/`base64` flags. Set via `POST /webhook/set/{instance}`.
* **Global webhook**: one URL from env (`WEBHOOK_GLOBAL_*`), **no custom
  headers**. Disabled here (`WEBHOOK_GLOBAL_ENABLED=false`), because the
  orchestrator is the only consumer (design §15).
* Delivery is awaited with retries. Upstream defaults are kept: 10
  attempts, exponential backoff capped at 300 s, about 20 minutes in total
  [R21]. That rides out an orchestrator restart instead of dropping a
  video. 400/401/403/404/422 responses are not retried.

Evolution posts to `http://orchestrator:8000/…` **inside the compose
network**. The webhook never leaves the host and has no public route.

### 6.2 Enable it when the orchestrator is deployed (LIVESTACK-24)

1. Set `ORCHESTRATOR_IMAGE` and `REVIEW_UI_IMAGE` in `.env` (CI publishes
   `ghcr.io/jbresolin/baja-orchestrator` and `…/baja-review-ui` with tags
   `main`, the short commit SHA and `v*`) and start both:
   `sudo docker compose --profile orchestrator up -d orchestrator review-ui`
2. Point the instance webhook at it, with the shared secret:
   ```bash
   curl -sS -X POST "https://$EVO_DOMAIN/webhook/set/$BVP_EVOLUTION_INSTANCE" \
     -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
     -d '{"webhook":{"enabled":true,"url":"http://orchestrator:8000/intake/evolution-webhook","headers":{"X-Webhook-Secret":"'"$BVP_WEBHOOK_SECRET"'"},"byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","CONNECTION_UPDATE"]}}'
   ```
3. Check: `GET /webhook/find/$BVP_EVOLUTION_INSTANCE` shows that URL, and a
   document sent to the bot shows up as a job in the orchestrator.

### 6.3 Keep `base64` off

`base64` is a property of the instance webhook. When it is on, Evolution
downloads *every* incoming media into a Node Buffer and embeds it in the
payload [R22]. A 2 GB WhatsApp document (design §3) would OOM-kill Evolution
at its 1 GiB limit. S3 upload is off for the same reason (it buffers
too). The orchestrator streams and decrypts documents from the WhatsApp CDN
itself (design §15).

---

## 7. Rotating secrets

* **Evolution API key**: change `EVOLUTION_API_KEY` in `.env`, then
  `sudo docker compose up -d evolution`. The orchestrator uses the instance
  token, so it is not affected.
* **Webhook secret**: change `BVP_WEBHOOK_SECRET`, then
  `sudo docker compose --profile orchestrator up -d orchestrator`, then
  repeat §6.2 step 2 with the new value.
* **Reviewers**: §2.6.

---

## 8. Memory, disk, and where the caps are enforced

### 8.1 Memory (4 GB box)

| Container | Limit | Notes |
| --- | --- | --- |
| evolution | 1024 MiB | Node + Baileys, one instance. Heap capped at 640 MiB (`NODE_OPTIONS`), so GC kicks in before an OOM. Media is never buffered (§6.3). |
| postgres | 512 MiB | `shared_buffers=128MB`, `max_connections=50` (upstream: 1000) |
| redis | 320 MiB | `maxmemory 160mb`, `volatile-lru`. Headroom for the AOF-rewrite fork. Session keys have no TTL and are never evicted. |
| orchestrator | 768 MiB | FastAPI + SQLite. Must stream downloads/uploads to disk, never hold a file in RAM. |
| caddy | 192 MiB | TLS + static files |
| **Containers total** | **2816 MiB** | |
| Host | ~1.2 GiB | kernel, dockerd/containerd, tailscaled, sshd, journald, page cache (which serves the proxies) |
| Swap | 2 GiB | host only (`memswap_limit = mem_limit`) |

Check with `docker stats --no-stream`. Evolution + Postgres + Redis are the
big three (design §10). If Evolution sits above about 800 MiB in steady
state, look at `DATABASE_SAVE_*` before raising limits.

### 8.2 Disk (70 GB) and caps (design §10, §15)

Throughput assumption: 200 passes × 500 MB = 100 GB/day through the VPS,
pulled twice = 200 GB egress (about 4.5 h at 100 Mbps, spread over the day).
The VPS is **transit only**, so this volume never sits on disk at once.

| Consumer | Budget | Enforced by |
| --- | --- | --- |
| Ubuntu + Docker images/layers | ~10 GB | — |
| Swap file | 2 GB | fixed |
| Evolution Postgres + Redis + instances | 1–3 GB | grows with saved messages (`DATABASE_SAVE_*`) |
| Container logs | ≤ 30 MB per container | json-file rotation (`x-logging` in compose) |
| journald | ≤ 500 MB | `SystemMaxUse` (§2.2) |
| **Inbox** `/srv/pipeline/inbox` (raws waiting for a 2nd holder) | **hard cap 25 GiB**, normally near-empty | **Kernel:** 25 GiB loop filesystem (§2.3), the same size as `BVP_INBOX_CAP_GB=25`, which the orchestrator also enforces. **Soft cap: orchestrator** at `BVP_INBOX_HOLD_GB=22`, where the bot tells senders to hold. Only fills if all workers are offline. |
| **Proxies + thumbs** | **~6 GB per 200-pass event** | **Orchestrator:** evicts the oldest *completed* jobs first when root free space < `BVP_PROXY_EVICT_MIN_FREE_GB=10`. Any holder regenerates on request. |
| **Outbox** | fallback only | **Orchestrator:** deleted after the streaming PC's ACK |
| Jobs DB `/srv/pipeline/db/` | small (SQLite WAL) | — |
| Free headroom | ≥ 10 GB | same threshold as proxy eviction |

Watch: `df -h / /srv/pipeline/inbox` and `sudo du -sh /srv/pipeline/*`.

---

## 9. Rollback

Keep the TrueNAS stack **stopped, not deleted**, for at least two weeks.
Its database was only dumped, never migrated, so the old image still
matches it.

**Clean rollback** (VPS never reached `open`, or only for minutes):

```bash
# VPS
sudo docker compose stop evolution
# TrueNAS (restore the restart policy you recorded in §1.1)
sudo docker update --restart=unless-stopped "$EVO_C"
sudo docker start "$EVO_C"
sudo docker exec "$EVO_C" wget -qO- --header "apikey: $EVO_KEY" "http://127.0.0.1:8080/instance/connectionState/$EVO_NAME" ; echo
```

Restart the TrueNAS app in the UI if it is one.

**Late rollback** (VPS ran connected for hours or days): the Signal session
state on TrueNAS is now stale. It usually reconnects, because the identity
(`creds`) didn't change, but some chats may show "waiting for this message"
until keys renegotiate. Prefer fixing forward on the VPS. If you must go
back and it won't connect, re-link with a QR on TrueNAS. Same number, new
linked device.

Never do either rollback while both sides are running, and never use
`logout`/`delete` to "reset" (§0).

**Cleanup after success** (after the two-week window): delete
`~/evo-migration` on TrueNAS and `/root/evo-migration` on the VPS. They
contain the session keys.

---

## 10. Upgrading Evolution later

* Bump the tag in `docker-compose.yml` deliberately. Never use `latest`.
* **2.4.0 requires license activation** against the Evolution Foundation
  licensing server before serving API traffic (business endpoints return
  503 until activated). It is RC-only as of this writing [R3]. Evaluate it
  before upgrading.
* Before any upgrade: `pg_dump` (as §3.2a, against the VPS container) and a
  copy of the Redis snapshot (`redis-cli --rdb`).

---

## 11. Orchestrator ↔ Caddy

**`docs/api.md` is the authority** for the orchestrator's routes, auth
schemes, payloads and data layout (design §15 settles any conflict). Only
the Caddy-side facts live here. If one of them changes, update the
`Caddyfile` in the same PR.

**Listener layout**

| Listener | Reachable from | Routes |
| --- | --- | --- |
| `:443` `{$PIPELINE_DOMAIN}` | internet | `/review*` → review-ui:8001 (reviewer basic auth); `/proxies/*`, `/thumbs/*` → static (reviewer basic auth); `POST /enroll` → orchestrator (no auth, 16 KB body cap); `/` → redirect to `/review/`; anything else 404 |
| `:443` `{$EVO_DOMAIN}` | internet | Evolution API (`apikey` header required), `/manager` + `/assets/*` (manager basic auth) |
| `${TAILSCALE_IP}:8080` | tailnet only | `/inbox/*`, `/outbox/*` → static behind `forward_auth`; everything else → orchestrator |
| `orchestrator:8000` | compose network only | Caddy and Evolution's webhook (`POST /intake/evolution-webhook`) |

**forward_auth (`/inbox/*`, `/outbox/*`)**: Caddy sends `GET /auth/file` to
`orchestrator:8000` with the client's original headers (including
`Authorization: Bearer …`) plus `X-Forwarded-Method` and `X-Forwarded-Uri`
(the requested path) [R23]. A 2xx lets Caddy serve the file, and an
`X-Worker-Id` response header is copied onto the request for the access
log. Any other status is returned to the worker unchanged.

**Reviewer requests**: Caddy checks basic auth against
`/srv/pipeline/reviewers.txt` (format in §2.6), then proxies `/review*` to `review-ui:8001`
with the client's `Authorization` header unchanged, plus `X-Reviewer:
<username>`.

**`/enroll`**: proxied with `X-Forwarded-For` set by Caddy. The
orchestrator rate-limits per source IP from that header, so it should only
trust it from Caddy.

**Dot-file rule**: Caddy's file_server hides `.*` (plus `*.part`,
`*.part.json`) under `/srv/pipeline` [R24]. Writers create files as
dot-files and rename them when complete, so Caddy never serves half a file.
Range requests (resume, seeking) are handled by file_server.

---

## 12. References

Evolution API (official docs live at docs.evolutionfoundation.com.br; doc.evolution-api.com redirects there):

* [R1] Releases (v2.3.7 = Latest; 2.4.0-rc1/rc2 pre-release): https://github.com/EvolutionAPI/evolution-api/releases
* [R2] Docker Hub tags (`v2.3.7` 2025-12-05; `latest` 2026-05-06): https://hub.docker.com/r/evoapicloud/evolution-api/tags
* [R3] 2.4.0-rc1 notes (license activation, "do not deploy to production"): https://github.com/EvolutionAPI/evolution-api/releases/tag/2.4.0-rc1
* [R4] Auth-state selection (provider → Redis `SAVE_INSTANCES` → Prisma): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts (`defineAuthState`)
* [R5] Prisma auth state: `creds` in `Session`, other keys in Redis hash or `INSTANCE_DIR` files: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/utils/use-multi-file-auth-state-prisma.ts ; key naming: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/cache/rediscache.ts , https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/server.module.ts
* [R6] Instances loaded `WHERE clientName = DATABASE_CONNECTION_CLIENT_NAME`: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/services/monitor.service.ts
* [R7] Docs index (no backup/migration page): https://docs.evolutionfoundation.com.br/llms.txt
* [R8] Webhook delivery (instance vs global, retries, headers only on instance): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.controller.ts
* [R9] Logout instance: https://docs.evolutionfoundation.com.br/evolution-api/logout-instance.md
* [R10] Docker install (`evolution_instances:/evolution/instances`): https://docs.evolutionfoundation.com.br/evolution-api/install/docker.md
* [R11] Env vars (docs): https://docs.evolutionfoundation.com.br/evolution-api/configuration/env.md ; defaults in code: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/config/env.config.ts ; upstream example: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/.env.example
* [R12] Image entrypoint runs DB migrations: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/Dockerfile
* [R13] Find webhook / set webhook (docs): https://docs.evolutionfoundation.com.br/evolution-api/get-webhook.md , https://docs.evolutionfoundation.com.br/evolution-api/set-webhook.md
* [R16] Set-webhook body schema actually validated in v2.3.7: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.schema.ts
* [R17] Connection state: https://docs.evolutionfoundation.com.br/evolution-api/get-connection-state.md
* [R18] Send text: https://docs.evolutionfoundation.com.br/evolution-api/send-text-message.md
* [R19] Fetch instances: https://docs.evolutionfoundation.com.br/evolution-api/fetch-all-instances.md
* [R20] Webhooks overview (instance vs global): https://docs.evolutionfoundation.com.br/evolution-api/configuration/webhooks.md
* [R21] Retry settings `WEBHOOK_RETRY_*` and defaults: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/.env.example
* [R22] Media buffered when `webhookBase64` is on (`messages.upsert`): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts

Other:

* [R14] Docker and ufw: https://docs.docker.com/engine/network/packet-filtering-firewalls/
* [R15] Redis: switching RDB → AOF on a live server: https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
* [R23] Caddy `forward_auth`: https://caddyserver.com/docs/caddyfile/directives/forward_auth
* [R24] Caddy `file_server` (`hide`, Range → 206): https://caddyserver.com/docs/caddyfile/directives/file_server ; `basic_auth` (hash cache enabled from the Caddyfile: https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/caddyauth/caddyfile.go): https://caddyserver.com/docs/caddyfile/directives/basic_auth ; `import`: https://caddyserver.com/docs/caddyfile/directives/import
