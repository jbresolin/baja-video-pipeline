# Event-day runbook

For João (operator part, English) and for the volunteers (Portuguese parts,
marked 🇧🇷). The message for people who *send* videos from their phones is
[`docs/volunteers-pt-BR.md`](../../docs/volunteers-pt-BR.md); pin it in the
volunteers' group.

Every VPS command below runs as root in `/opt/baja/infra/vps` after
loading the settings once per shell:

```bash
cd /opt/baja/infra/vps
set -a; . ./.env; set +a
API="http://$TAILSCALE_IP:8080"                       # Caddy's tailnet listener → orchestrator
ADMIN="Authorization: Bearer $BVP_ADMIN_TOKEN"
```

---

## T-7 days: everything in place

### 1. VPS health and backups

```bash
sudo ./compose-smoke.sh                      # must end with ALL CHECKS PASSED
systemctl list-timers baja-backup.timer      # next run is tonight
ls -1 /var/backups/baja/ | tail -3           # recent nightly folders, none "-incomplete"
```

Practise a DB restore on a copy (restore.md, "Practising a restore").
Check `.env` and `orchestrator.env` are current in the password manager.

### 2. Tailnet nodes

In the Tailscale admin console (*Machines*):

* The VPS is `tag:server`, online, key expiry disabled.
* Last event's workers: delete machines of PCs that will not come back
  (tagged nodes never expire by themselves, design §8).
* *Access controls* → the policy saves without test failures (the
  `tests` block in `infra/tailnet/acl.hujson` runs on save).
* *Settings → OAuth clients*: the orchestrator's client (`tag:enroller`,
  scope `auth_keys`) still exists; its id/secret are the
  `TAILSCALE_OAUTH_*` values in `.env`.

On the VPS: `tailscale status` lists the VPS itself, and
`tailscale ip -4` equals `TAILSCALE_IP` in `.env`.

### 3. Enrollment tokens (one per PC)

Ask each volunteer lending a PC for a name you will recognise on the fleet
page. One token per PC, valid for a week (`valid_for_hours`, max 720):

```bash
curl -s -H "$ADMIN" -H 'Content-Type: application/json' "$API/admin/enrollment-tokens" \
  -d '{"volunteer_name": "Ana - notebook", "role": "worker", "valid_for_hours": 168}'
```

The answer's `token` is shown **once**; send it to that volunteer only
(private message). Roles: `master` for João's desktop, `studio` for the
streaming PC, `worker` for everyone else. Each token works once.

🇧🇷 Mensagem para quem empresta o PC (mande junto com o token e o link do instalador):

> Oi! Obrigado por emprestar o PC para os vídeos da Baja 🙌
> 1. Baixe e rode o instalador `BajaWorker-Setup.exe` (link abaixo). O Windows vai
>    mostrar "O Windows protegeu o computador" (editor desconhecido): clique em
>    *Mais informações* → *Executar assim mesmo*.
> 2. No fim, abre uma página no navegador. Cole este código: `<token>`.
> 3. Escolha uma pasta num disco com espaço (ex.: `D:\Baja`) e quanto espaço o
>    programa pode usar (pelo menos 100 GB, se der).
> 4. Pronto. Deixe o PC ligado, na tomada e com internet durante o evento. Não precisa
>    abrir nada: o programa roda sozinho em segundo plano.
> Não apague essa pasta depois do evento até eu avisar que o backup terminou.

### 4. Reviewers

For each person reviewing videos, README §2.6 ("Add a reviewer"), then
restart the orchestrator so it reads the new file (Caddy reloads, the
orchestrator does not re-read `reviewers.txt` by itself):

```bash
sudo docker compose --profile orchestrator restart orchestrator
sudo ./compose-smoke.sh                      # "reviewers file" row lists everyone
```

Send each reviewer `https://<PIPELINE_DOMAIN>/review/` and their own
password. Shortcuts are on the job page (`?`).

### 5. Teams (`teams.csv`)

One `car_number,team_name,school` line per car, from the official entry
list. The orchestrator re-reads it when it changes; no restart:

```bash
sudo install -o 10001 -g 10001 -m 640 teams.csv /srv/pipeline/teams.csv
curl -s -u '<reviewer>:<password>' "https://$PIPELINE_DOMAIN/review/api/teams/42"   # → that car's team
```

A car missing from the file shows no team name on the overlay (design §18).

### 6. Inbox cap

```bash
df -h /srv/pipeline/inbox                    # its own ~25G filesystem
grep -E '^BVP_INBOX_(HOLD|CAP)_GB' .env      # 22 / 25 unless you changed them
df -h /                                      # at least 20 GB free before the event
```

The inbox only fills when no second holder takes raws (design §10), so the
real "cap check" is having enough workers with free space: at least the
master plus two volunteer PCs with ≥ 100 GB each for a 200-pass day.

---

## T-1 day: full dry run

Goal: one real video from a phone reaches the studio box as a clip.

1. Start every PC that will be used: the master, the studio (with OBS
   pointed at its clip folder) and the volunteers' workers. The fleet page
   (`/review/fleet`) shows each one **online** with a heartbeat of a few
   seconds; the studio row says `studio`.
2. From a phone, send a short test video **as a document** to the Baja Bot
   number (docs/volunteers-pt-BR.md). Within a few seconds the phone gets
   *✅ Vídeo recebido! Obrigado.*
3. In the review UI the job appears as `received`, then `awaiting_review`
   with a thumbnail and a playable proxy (made by its first holder). If it stays
   `received`, no worker is downloading: check the fleet page "Doing"
   column and the master's `logs\worker.log`.
4. Set in/out, prova `teste`, a real car number, passagem 1, and mark it
   ready. A holder renders it and pushes it to the studio: the job goes
   `claimed` → `uploaded`/`delivered` → `done`.
5. On the streaming PC: `teste\<car>\1.mp4` exists in the clip folder and
   is at the top of `latest.m3u`; OBS plays it with the team name in the
   overlay.
6. Send the same video once more as a normal *video* (not a document): the
   bot answers with the "envie como documento" message and the job shows
   the low-res flag. Discard that one in the review UI (a `done` job
   cannot be discarded; the first one simply stays in the DB under prova
   `teste`). On the streaming PC delete the `teste` folder; its line in
   `latest.m3u` drops off as real clips arrive.
7. `sudo ./compose-smoke.sh` once more, and check tonight's backup runs.

---

## During the event

### What the fleet page should look like

`https://<PIPELINE_DOMAIN>/review/fleet`, refreshing every 10 s:

* **Workers online**: every PC you expect, the master included. A row in
  grey = offline (no heartbeat for 45 s). Its leases expire and the work
  goes to someone else by itself.
* **VPS inbox**: near empty. It holds only raws still waiting for their
  second holder, so a growing bar means downloads are not keeping up.
* **Jobs by state**: `awaiting_review` is the reviewers' backlog; `ready`
  should drain within minutes; `failed` needs a reviewer decision (retry or
  discard; the job page shows the error).
* **Doing** column: `download_raw` / `make_proxy` / `process` per worker.
  The master shows downloads even when idle otherwise: it backfills every
  raw it does not hold yet, at the lowest priority.
* **near cap, no new raws** on a row: that PC is full; it still renders
  what it holds.

### "waiting for a holder of the raw to come back online"

Shown on a `ready` job (or a proxy request) when **both PCs holding its
raw are offline**. Only a holder can render it (design §4), so it waits;
nothing is lost. Bring one of the two back (the job page's event log names
them: `holder_confirmed` events), and it renders within a minute. If
neither can come back, and the master was not a holder yet, the master
gets it with its backfill once it is online.

### Inbox filling up (bot says "espere alguns minutos")

Above `BVP_INBOX_HOLD_GB` the bot asks senders to hold; above
`BVP_INBOX_CAP_GB` it refuses videos (they must be re-sent later). Fix the
cause, not the cap:

1. Fleet page: are workers online and accepting new raws? Start more PCs,
   or free disk on the ones marked "near cap".
2. Is the master online? It downloads everything.
3. Raws leave the inbox when two holders confirmed them (checked every few
   seconds), so the bar drops by itself once workers catch up.

### Freeing disk

**VPS** (`df -h / /srv/pipeline/inbox`, `sudo du -sh /srv/pipeline/*`):

* Proxies are evicted automatically, oldest completed jobs first, when
  free space drops below `BVP_PROXY_EVICT_MIN_FREE_GB`. Any holder
  re-renders one on request ("Re-render proxy" on the job page).
* Outbox clips are deleted after the studio's ACK. A growing outbox means
  the studio is offline: bring it back, it pulls them.
* Never delete files in `inbox/` by hand while jobs are live: that raw
  may have only one holder yet.
* Safe extras: `sudo docker image prune -f`, `sudo journalctl --vacuum-size=200M`,
  and old backups in `/var/backups/baja/` (copy them off first).

**A volunteer's PC** marked "near cap": held raws are never evicted
(design §9). Raise the storage cap on its setup page
(`http://127.0.0.1:8700` on that PC), or free other space on that disk.
Rendered clips are deleted by the worker once delivered.

### Re-issuing a token

A PC was reinstalled, its folder deleted, or the token expired before use:

1. Mint a new token for it (T-7 §3). Use a slightly different
   `volunteer_name` (e.g. "Ana - notebook (2)") so you can tell the two
   rows apart on the fleet page.
2. On that PC, open `http://127.0.0.1:8700` and paste it. If the PC was
   already enrolled with a working `worker.json`, it needs no new token at
   all: just start the service again.
3. The old row stays on the fleet page as offline for good. Its leases
   expire and go to other workers by themselves. Jobs it held still count
   it as a holder: if its folder is really gone, those raws now live only
   on their other holder (and on the master, once its backfill reaches
   them), so keep that other holder online until the master has them.

A leaked token is single use and expires (`valid_for_hours`), so the fix
is simply not to send it again.

### Quick commands

```bash
curl -s -H "$ADMIN" "$API/metrics" | grep -E '^bvp_(jobs|workers_online)'   # numbers at a glance
curl -s -H "$ADMIN" "$API/events?limit=30"                                  # latest events
sudo docker compose --profile orchestrator logs --since 10m orchestrator
sudo docker compose logs --since 10m evolution | grep -iE 'error|close'
```

🇧🇷 Para quem emprestou o PC, durante o evento: *Deixe o PC ligado, na tomada,
com internet e sem entrar em suspensão. Se precisar desligar, me avise antes:
os vídeos que estão no seu PC esperam ele voltar.*

---

## After the event

### 1. Wait for the master to hold everything

The master backfills every raw it does not hold, at the lowest priority,
after the live work. On the fleet page its held-raws count should reach
the number of non-discarded jobs, and its "Doing" column should go idle:

```bash
curl -s -H "$ADMIN" "$API/metrics" | grep '^bvp_jobs'    # add up all states except discarded
```

Keep the volunteers' PCs online until then: they are the backfill's sources
once the VPS inbox copy is gone.

### 2. Keep the job list with the raws

Raws are named `<job_id>.<ext>`; only the jobs DB says which pass each
one is (`jobs` table: id, prova, car_number, passagem, sha256, sender).
Take a fresh backup and copy its DB next to the archive. From an admin
device on the tailnet (the master is `tag:worker`, which may not SSH to the
VPS):

```bash
ssh root@<vps-tailscale-ip> /opt/baja/infra/vps/backup.sh
ssh root@<vps-tailscale-ip> 'ls -1 /var/backups/baja/ | tail -1'           # → <stamp>
scp root@<vps-tailscale-ip>:/var/backups/baja/<stamp>/bvp.sqlite3 ./event-<YYYY-MM-DD>-bvp.sqlite3
```

Put that file in the same TrueNAS folder as the raws (step 3).

### 3. Archive the master's folder to TrueNAS

TrueNAS is not on the tailnet (design §2); copy over the home LAN, from
João's desktop. The source is the master's **working folder** chosen on
its setup page (shown on `http://127.0.0.1:8700`); `raws\` is what matters.

From Windows (PowerShell or cmd), to the TrueNAS SMB share:

```bat
robocopy "D:\Baja\master\raws" "\\truenas\baja-archive\<YYYY-MM-DD event>\raws" /E /Z /COPY:DAT /DCOPY:T /R:3 /W:10 /MT:8 /XF .* *.part /LOG:"%USERPROFILE%\Desktop\baja-archive.log" /TEE
```

(`D:\Baja\master` stands for the master's working folder. `/Z` resumes
a big file after a network blip, `/MT:8` copies 8 files at once, and
`/XF .* *.part` skips downloads that never finished.)

Or from WSL/Linux with rsync over SSH to TrueNAS:

```bash
rsync -a --info=progress2 --exclude='.*' --exclude='*.part' \
  /mnt/d/Baja/master/raws/ truenas:/mnt/tank/baja-archive/<YYYY-MM-DD-event>/raws/
```

Verify before deleting anything: the file count matches, and robocopy's
summary shows `FAILED 0` (or rsync exits 0). For a checksum check, compare
with the DB (each raw's SHA-256 is in the `jobs` table):

```powershell
Get-FileHash -Algorithm SHA256 \\truenas\baja-archive\<event>\raws\* | Export-Csv "$HOME\Desktop\archive-hashes.csv"
```

### 4. Prune the VPS

Only after step 3 is verified:

```bash
sudo find /srv/pipeline/proxies /srv/pipeline/thumbs -type f -delete   # review copies; holders can re-render
sudo ls -la /srv/pipeline/inbox /srv/pipeline/outbox                    # should already be empty
sudo docker image prune -f
```

Keep `db/bvp.sqlite3` (the next event starts from it or from a fresh DB,
your choice: to start fresh, stop the orchestrator, move the file aside,
start it). Keep the nightly backups' timer running.

### 5. Release the volunteers' PCs

🇧🇷 Mensagem para quem emprestou o PC (só depois do passo 3 conferido):

> Obrigado demais pela ajuda no evento! 🎉 O backup dos vídeos terminou. Agora você
> pode desinstalar o *BajaWorker* (Configurações → Aplicativos) e apagar a pasta que
> escolheu na instalação (ex.: `D:\Baja`). Se quiser ajudar de novo no próximo
> evento, é só manter instalado.

Then remove their machines from the Tailscale admin console (or leave the
ones that will return).
