# Baja staging VPS: Evolution + Caddy (LIVESTACK-30)

Copy-paste runbook for moving the **Baja Bot** Evolution API instance from
the TrueNAS box at home to the Baja staging VPS **without re-scanning the QR
code**, and for running it next to the pipeline orchestrator behind Caddy.
Design context: [`docs/design.md`](../../docs/design.md) §2, §3, §10, §13.

Files in this directory:

| File | What it is |
| --- | --- |
| `docker-compose.yml` | Evolution API v2.3.7 + Postgres + Redis, Caddy, orchestrator stub |
| `Caddyfile` | Public Evolution vhost, public review/proxies vhost, tailnet-only worker API |
| `.env.example` | Every variable, with comments. Copy to `.env` |

```
                 internet                                    tailnet (100.x)
                    │ 80/443                                      │ :8080
              ┌─────▼──────────────────── VPS ────────────────────▼─────┐
              │  caddy ── {EVO_DOMAIN} ──────────► evolution:8080        │
              │    │      (apikey hdr; /manager = basic auth)  │         │
              │    │                                           │ net     │
              │    ├──── {PIPELINE_DOMAIN}                     │ evolution_db
              │    │       /review*  ─► orchestrator:8000      ├─ postgres
              │    │       /proxies/* ─► file_server           └─ redis
              │    └──── http://:8080 (TAILSCALE_IP only)                │
              │            /inbox/* /outbox/* ─► forward_auth + file_server
              │            everything else    ─► orchestrator:8000       │
              │  evolution ──instance webhook──► orchestrator (internal) │
              │  evolution ──global webhook────► n8n / BajaBot (home)    │
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
* **Two webhook consumers without a fan-out service.** Evolution v2 has
  exactly one *instance* webhook (one URL, stored in Postgres) and one
  *global* webhook (one URL, from env). Both fire for every enabled event
  [R8]. Instance webhook → orchestrator, global webhook → n8n. Details and
  reasoning: §6.
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
Check whether Postgres/Redis are **shared with other apps** (e.g. n8n). The
procedure below never stops them and only copies Evolution's data, so
sharing is fine. Just know it.

### 1.2 Record the values that must match on the VPS

The process environment overrides the defaults baked into the image at
`/evolution/.env`. Read both:

```bash
sudo docker inspect "$EVO_C" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(SERVER_URL|AUTHENTICATION_API_KEY|DATABASE_|CACHE_|PROVIDER_|CONFIG_SESSION_|WEBHOOK_)' | sort
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
EVO_KEY=<AUTHENTICATION_API_KEY>; EVO_NAME=<instance name, e.g. BajaBot>
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

### 1.4 Record the instance, its webhook and the key counts

```bash
sudo docker exec "$PG_C" psql -U "$PGU" -d "$PGD" -c "SELECT id, name, \"connectionStatus\", \"clientName\", token FROM \"$SCHEMA\".\"Instance\";"
sudo docker exec "$PG_C" psql -U "$PGU" -d "$PGD" -c "SELECT \"sessionId\", length(creds) FROM \"$SCHEMA\".\"Session\";"
sudo docker exec "$EVO_C" wget -qO- --header "apikey: $EVO_KEY" "http://127.0.0.1:8080/webhook/find/$EVO_NAME" ; echo
```

* `id` = the instance id (`INSTANCE_ID=<id>`). `token` = the instance token
  (`EVOLUTION_INSTANCE_TOKEN` in the VPS `.env`).
* From the webhook JSON, save `url`, `events`, `webhookByEvents`,
  `webhookBase64`, `headers` [R13]. They define what n8n receives today and
  feed §6.

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

### 1.5 Decide how the VPS reaches n8n

Evolution on the VPS must POST to BajaBot's n8n Webhook node at home. Pick one:

1. **n8n already has a public HTTPS URL** (reverse proxy, Cloudflare Tunnel,
   …). Use it. **Recommended** if it exists. Nothing new on the tailnet.
2. **Put n8n on the tailnet** as a dedicated tagged node (e.g. a Tailscale
   sidecar `tag:home-n8n`, ACL `tag:server → tag:home-n8n:5678` only). This
   deviates from design §2 ("TrueNAS out of the tailnet"), so it is the
   owner's call.

Test reachability **from the VPS** without triggering the flow. A GET on a
POST-only n8n webhook returns 404 *from n8n*, which proves the path works:

```bash
curl -sS -i "$N8N_WEBHOOK_URL" | head -5
```

---

## 2. Prepare the VPS (any time before the cutover)

Run all VPS steps as root (`sudo -i`). `.env` is mode 600, and later steps
load it into the shell with `set -a; . ./.env; set +a`.

### 2.1 Checkout and directories

```bash
sudo git clone https://github.com/jbresolin/baja-video-pipeline.git /opt/baja
cd /opt/baja/infra/vps
sudo mkdir -p /srv/pipeline/outbox /srv/pipeline/proxies /srv/pipeline/inbox /root/evo-migration
```

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

The inbox gets its own loop-mounted ext4 file. When it is full, writes fail
with ENOSPC inside the inbox only, and Postgres/Redis/Evolution on the root
filesystem keep working. The soft cap (bot asks senders to hold) is the
orchestrator's job, see §8.

```bash
sudo fallocate -l 25G /srv/pipeline/inbox.img
sudo mkfs.ext4 -m 0 -L baja-inbox /srv/pipeline/inbox.img
echo '/srv/pipeline/inbox.img /srv/pipeline/inbox ext4 loop,noatime,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount /srv/pipeline/inbox
```

Make Docker wait for that mount at boot, so containers never write into the
empty directory underneath it:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nRequiresMountsFor=/srv/pipeline/inbox\n' | sudo tee /etc/systemd/system/docker.service.d/10-baja-inbox.conf
sudo systemctl daemon-reload
```

Ownership: the orchestrator PR states its container UID. Then
`sudo chown -R <uid>:<uid> /srv/pipeline/{inbox,outbox,proxies}`.

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
openssl rand -hex 24      # run once per secret: POSTGRES_PASSWORD, EVOLUTION_WEBHOOK_SECRET
sudo docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext 'manager-password'
sudo docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext 'reviewer-password'
sudo nano .env            # fill in everything; put the bcrypt hashes in SINGLE quotes
```

Then validate. This must print nothing:

```bash
sudo docker compose config -q
```

### 2.6 Pull images, create volumes, start Caddy early

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

### 3.8 Phase A webhook fix-up (only if needed)

The instance webhook came over in Postgres, still pointing at n8n's *old*
URL. If that URL only works inside your home LAN, switch it to the URL from
§1.5. Use the **same** events/`byEvents`/`base64` you recorded, except set
`base64` to `false` if BajaBot doesn't need it (§6.3):

```bash
curl -sS -X POST "https://$EVO_DOMAIN/webhook/set/$EVOLUTION_INSTANCE_NAME" \
  -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
  -d '{"webhook":{"enabled":true,"url":"'"$N8N_WEBHOOK_URL"'","byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","CONNECTION_UPDATE"]}}'
```

The body is **nested under `"webhook"`** with `byEvents`/`base64`. That's
what v2.3.7 validates [R16]. The docs page shows a flat body [R13], which is
wrong for this version.

---

## 4. Verify: connected without a QR

```bash
curl -sS -H "apikey: $EVOLUTION_API_KEY" "https://$EVO_DOMAIN/instance/connectionState/$EVOLUTION_INSTANCE_NAME" ; echo
```

Must be `{"instance":{"instanceName":"…","state":"open"}}` [R17]. Then:

1. Phone of the Baja Bot number → *Linked devices*. The **same** device entry
   as before ("Evolution API"/Chrome), with recent activity and **no new
   device**.
2. Send a test message from the bot [R18]:
   ```bash
   curl -sS -X POST "https://$EVO_DOMAIN/message/sendText/$EVOLUTION_INSTANCE_NAME" \
     -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
     -d '{"number":"55XXXXXXXXXXX","text":"VPS migration test"}'
   ```
3. Reply to the bot from that phone. The reply must decrypt (Evolution logs
   show no `Bad MAC`/"waiting for this message"), and BajaBot's n8n
   *Executions* list shows the run.

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
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' "http://<vps-public-ip>:8080/"    # must time out / refuse
```

Manager UI: `https://$EVO_DOMAIN/manager`. First the basic auth, then
Evolution's own login with the API key.

---

## 6. Webhooks: one for n8n, one for the orchestrator

### 6.1 How Evolution v2 delivers, and the choice

From the v2.3.7 source [R8] and docs [R13][R20]:

* **Instance webhook**: one per instance (`"Webhook".instanceId` is
  unique), **one URL**, custom `headers` supported, own event list, plus
  `byEvents`/`base64` flags. Set via `POST /webhook/set/{instance}`.
* **Global webhook**: **one URL** from env (`WEBHOOK_GLOBAL_*`), fires for
  every instance, event list from `WEBHOOK_EVENTS_*`, **no custom headers**.
* For each event, the instance webhook is sent **first and awaited,
  including retries**. After that the global webhook is sent. Both fire,
  independently.
* There are also brokers (RabbitMQ/SQS/Kafka/WebSocket), so "two webhooks"
  is not the only way to fan out.

| Option | Verdict |
| --- | --- |
| **A. Instance → orchestrator, global → n8n** | **Recommended.** Native, no extra service. The orchestrator gets the header secret and goes first, so home-internet outages (n8n unreachable) can never delay intake. n8n lags the orchestrator by milliseconds. |
| B. Instance → n8n, global → orchestrator | Rejected. Each event waits for n8n's retries before reaching the orchestrator, which brings back exactly the dependency on home internet that v2 removes. And the orchestrator could not authenticate the call. |
| C. Small fan-out relay | Unnecessary. One more process and failure point for what A gives natively. |
| D. RabbitMQ | Rewrites both consumers and adds roughly 150 MB RAM. Not worth it at this scale. |

Retries are bounded in `docker-compose.yml` (6 attempts, max 60 s
backoff). If the orchestrator is down, n8n is delayed by at most about
2 minutes per event, not the default of about 20 [R21].

### 6.2 Phase B: when the orchestrator is deployed (LIVESTACK-24)

Do it at a quiet time. Between step 2 and step 3 n8n misses a few seconds of
events, which is better than getting them twice (duplicate bot replies).

1. Set `ORCHESTRATOR_IMAGE` in `.env` and start it:
   `sudo docker compose --profile orchestrator up -d orchestrator`
2. Move the **instance** webhook to the orchestrator (internal URL, never
   public; path per the orchestrator PR):
   ```bash
   curl -sS -X POST "https://$EVO_DOMAIN/webhook/set/$EVOLUTION_INSTANCE_NAME" \
     -H "apikey: $EVOLUTION_API_KEY" -H 'content-type: application/json' \
     -d '{"webhook":{"enabled":true,"url":"http://orchestrator:8000/api/v1/evolution/webhook","headers":{"x-webhook-secret":"'"$EVOLUTION_WEBHOOK_SECRET"'"},"byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","CONNECTION_UPDATE"]}}'
   ```
3. Move **n8n** to the global webhook. In `.env`:
   `N8N_GLOBAL_WEBHOOK_ENABLED=true`, `N8N_WEBHOOK_URL`,
   `N8N_WEBHOOK_BY_EVENTS` (= the old `webhookByEvents`), and
   `WEBHOOK_EVENTS_*=true` for exactly the old `events` list (§1.4). Then:
   `sudo docker compose up -d evolution` (recreates it, reconnects in a few seconds).
4. Check: `GET /webhook/find/$EVOLUTION_INSTANCE_NAME` shows the
   orchestrator URL. A message to the bot shows up in the orchestrator
   logs **and** in n8n Executions.

Note: the global webhook can't send headers. If BajaBot's Webhook node
used header auth, switch it to a long random path segment.

### 6.3 Keep `base64` off

`base64` is a property of the **instance** webhook. When it is on,
Evolution downloads *every* incoming media into a Node Buffer and puts it in
the payload that both consumers get [R22]. A 2 GB WhatsApp document
(design §3) would OOM-kill Evolution at its 1 GiB limit. S3 upload is off
for the same reason (it buffers too). If BajaBot relies on `base64` today
(§1.4 `webhookBase64: true`), change the flow to fetch small media on
demand with `POST /chat/getBase64FromMediaMessage/{instance}` before
Phase B, and set `base64:false` already in §3.8 if you can.

---

## 7. Repoint n8n (BajaBot)

Two directions:

1. **n8n → Evolution** (sending messages, etc.): in every Evolution
   credential (community Evolution node) and every HTTP Request node that
   calls Evolution, change the base URL from the TrueNAS address to
   `https://<EVO_DOMAIN>`. Keep the `apikey` header. The global key is
   unchanged for the cutover, and instance tokens came over with Postgres.
   Search the exported workflow JSON for the old host to catch them all.
2. **Evolution → n8n** (incoming events): Phase A = instance webhook
   (§3.8), Phase B = global webhook (§6.2).

Test: message the bot → the flow runs → the bot's reply arrives. Later, if
you want, rotate `EVOLUTION_API_KEY`: change `.env`,
`docker compose up -d evolution`, then update n8n.

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

### 8.2 Disk (70 GB) and caps (design §10)

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
| **Inbox** (raws waiting for a 2nd holder) | **hard cap 25 GiB**, normally near-empty | **Kernel:** 25 GiB loop filesystem (§2.3). **Soft cap: orchestrator** at `INBOX_HOLD_GB=22`, where the bot tells senders to hold. Only fills if all workers are offline. |
| **Proxies** | **~6 GB per 200-pass event** | **Orchestrator:** evicts the oldest *completed* jobs first when root free space < `PROXY_EVICT_MIN_FREE_GB=10`. Any holder regenerates on request. |
| **Outbox** | fallback only | **Orchestrator:** deleted after the streaming PC's ACK |
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

Then switch n8n's base URL back (§7) and restart the TrueNAS app in the UI
if it is one.

**Late rollback** (VPS ran connected for hours or days): the Signal session
state on TrueNAS is now stale. It usually reconnects, because the identity
(`creds`) didn't change, but some chats may show "waiting for this message"
until keys renegotiate. Prefer fixing forward on the VPS. If you must go
back and it won't connect, re-link with a QR on TrueNAS. Same number, new
linked device, and BajaBot keeps working after its webhook is checked.

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

## 11. Contract for the orchestrator PR (LIVESTACK-24)

What this stack expects. Proposed names. If the orchestrator PR changes
them, update `Caddyfile`, `docker-compose.yml` and §6.2 in the same PR.

* **Service** `orchestrator`, listens on `0.0.0.0:8000` on the `edge`
  network. Image from `ORCHESTRATOR_IMAGE`. Extra settings in
  `infra/vps/orchestrator.env` (optional file). State lives in
  `/var/lib/orchestrator`.
* **Evolution webhook** `POST /api/v1/evolution/webhook` (internal only).
  Reject requests without `x-webhook-secret: $EVOLUTION_WEBHOOK_SECRET`
  (401, which is non-retryable for Evolution). **Reply 2xx within a few
  seconds and do the download asynchronously.** Evolution waits for this
  response (timeout 30 s, then retries) before it notifies n8n.
* **Media download**: the payload has **no base64** (§6.3). Don't use
  `getBase64FromMediaMessage` for documents (Evolution would buffer up to
  2 GB). Stream the encrypted file from the WhatsApp CDN (`url`/`directPath`
  in `documentMessage`) and decrypt while streaming with `mediaKey`
  (AES-256-CBC, keys from HKDF-SHA256 with info "WhatsApp Document Keys").
  Verify `fileEncSha256`/`fileSha256`, and check it against Baileys'
  `downloadContentFromMessage`. Send bot replies via
  `http://evolution:8080/message/sendText/{instance}` with
  `EVOLUTION_INSTANCE_TOKEN`.
* **File auth for Caddy**: `GET /api/v1/auth/file` (forward_auth). It gets
  the worker's `Authorization: Bearer …` plus `X-Forwarded-Method` /
  `X-Forwarded-Uri` [R23]. Return 2xx (optionally with `X-Worker-Id`) to
  allow, 401/403 to deny.
* **Paths/URLs**: raws in `/srv/pipeline/inbox/<name>` are served to workers
  as `http://$TAILSCALE_IP:8080/inbox/<name>`. Outbox likewise at
  `/outbox/`. Proxies `/srv/pipeline/proxies/<name>` are served as
  `https://$PIPELINE_DOMAIN/proxies/<name>` (reviewer basic auth). Write to
  a **dot-file then rename**: Caddy hides `.*` [R24], so it never serves a
  half-written file. HTTP Range is handled by Caddy's file_server.
* **Review UI** under `/review`. Caddy has already authenticated the
  reviewer and passes the user name in `X-Reviewer`.
* **Caps**: `INBOX_HOLD_GB` (soft, bot says hold), `INBOX_CAP_GB` (hard, the
  filesystem size), `PROXY_EVICT_MIN_FREE_GB`. See §8.2.

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
* [R8] Webhook delivery (instance then global, retries, headers only on instance): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.controller.ts
* [R9] Logout instance: https://docs.evolutionfoundation.com.br/evolution-api/logout-instance.md
* [R10] Docker install (`evolution_instances:/evolution/instances`): https://docs.evolutionfoundation.com.br/evolution-api/install/docker.md
* [R11] Env vars (docs): https://docs.evolutionfoundation.com.br/evolution-api/configuration/env.md ; defaults in code: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/config/env.config.ts ; upstream example: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/.env.example
* [R12] Image entrypoint runs DB migrations: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/Dockerfile
* [R13] Find webhook / set webhook (docs): https://docs.evolutionfoundation.com.br/evolution-api/get-webhook.md , https://docs.evolutionfoundation.com.br/evolution-api/set-webhook.md
* [R16] Set-webhook body schema actually validated in v2.3.7: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.schema.ts
* [R17] Connection state: https://docs.evolutionfoundation.com.br/evolution-api/get-connection-state.md
* [R18] Send text: https://docs.evolutionfoundation.com.br/evolution-api/send-text-message.md
* [R19] Fetch instances: https://docs.evolutionfoundation.com.br/evolution-api/fetch-all-instances.md
* [R20] Webhooks overview (instance vs global, `webhook_by_events`): https://docs.evolutionfoundation.com.br/evolution-api/configuration/webhooks.md
* [R21] Retry settings `WEBHOOK_RETRY_*`: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/.env.example
* [R22] Media buffered when `webhookBase64` is on (`messages.upsert`): https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts

Other:

* [R14] Docker and ufw: https://docs.docker.com/engine/network/packet-filtering-firewalls/
* [R15] Redis: switching RDB → AOF on a live server: https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
* [R23] Caddy `forward_auth`: https://caddyserver.com/docs/caddyfile/directives/forward_auth
* [R24] Caddy `file_server` (`hide`, Range → 206): https://caddyserver.com/docs/caddyfile/directives/file_server ; `basic_auth` (hash cache enabled from the Caddyfile: https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/caddyauth/caddyfile.go): https://caddyserver.com/docs/caddyfile/directives/basic_auth
