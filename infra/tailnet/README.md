# Baja tailnet runbook (LIVESTACK-29)

Use this runbook to set up a fresh Tailscale tailnet dedicated to the video
pipeline: the "Baja tailnet" in design §2 and §8. Run the steps in order.
Every step is either a command to copy or an exact admin-console path.

Files in this folder:

| File | What it is |
| --- | --- |
| `acl.hujson` | Policy file: tags, tag owners, grants, ACL tests |
| `mint_auth_key.md` | Spec for the orchestrator: OAuth token exchange and auth-key creation |
| `worker_join.md` | Spec for the installer: silent MSI, `tailscale up`, how the worker reads its IP and DNS name |

## Ports and tags (contract)

Every component uses these. The ACL in `acl.hujson` allows exactly these
flows and nothing else (Tailscale denies everything by default).

| Port | Host (tag) | Service | Who may connect |
| --- | --- | --- | --- |
| 443/tcp | VPS (`tag:server`) | Caddy, public side: review UI, proxies, `/enroll`, Evolution webhook. Reached over the internet, not the tailnet | admins (tailnet); public otherwise |
| 8080/tcp | VPS (`tag:server`), Tailscale IP only | Caddy, tailnet side: worker/studio API + inbox/outbox static files (plain HTTP; WireGuard encrypts) | `tag:worker`, `tag:studio`, admins |
| 8000/tcp | VPS, compose network only | Orchestrator (FastAPI). Not published on the host; only Caddy reaches it, so no grant | — |
| 22/tcp | VPS (`tag:server`) | SSH over the tailnet | admins only |
| 8710/tcp | each worker (`tag:worker`) | Worker file server (peer raw fetches) | `tag:worker` |
| 8720/tcp | streaming PC (`tag:studio`) | Studio receiver (clip push) | `tag:worker` |
| 8700/tcp | every worker and the studio, **127.0.0.1 only** | Worker setup/status page (design §17). Not on the tailnet, so no grant and no firewall rule | the local user only |

| Tag | Applied to | Enrolled by |
| --- | --- | --- |
| `tag:server` | Baja staging VPS | Admin, by hand (step 8) |
| `tag:studio` | Streaming PC | Admin, by hand the first time (step 9). Later re-installs can go through the installer with a studio enrollment token |
| `tag:worker` | Master desktop and volunteers' PCs | The installer, with a key minted by the orchestrator (`worker_join.md`) |
| `tag:enroller` | **No device.** Carried only by the orchestrator's OAuth client | — |

**Why `tag:enroller` exists.** An auth key minted by an OAuth client must
carry exactly the client's tags, or tags *owned by* the client's tags.
<https://tailscale.com/kb/1623/trust-credentials>

If the client carried both `tag:worker` and `tag:studio`, every key it
minted would carry both. Instead, the client is tagged `tag:enroller`, and
`tag:enroller` owns `tag:worker` and `tag:studio`. The client can then mint
a key for either tag, and only for those two. In practice this is the
"OAuth client restricted to `tag:worker` and `tag:studio`" that design §8
asks for.

Hostnames used below: `baja-vps`, `baja-studio`, and `baja-w-<enrollment
id>` for workers. `<tailnet>` stands for your tailnet DNS name, e.g.
`tail1234.ts.net` (step 2).

---

## 1. Create the tailnet

A tailnet belongs to the identity (account or domain) that signs up first.
Use a **dedicated identity** for the project, not your personal account. If
you used your personal account, you would land in your existing personal
tailnet.

1. Create the identity, for example a new Google account such as
   `baja-stream-ops@gmail.com`, or a GitHub organization for the team.
2. In a private browser window, open <https://login.tailscale.com/start>
   and sign in with that identity. Finish the signup questions. This
   creates the tailnet, and the identity becomes its **Owner**.
3. Skip "add your first device" for now.
4. Optional: invite a second admin (Admin console → **Users** → **Invite
   users**, role *Admin*), so that losing one login doesn't lock you out.

> Creating extra tailnets under an existing organization is a request-only
> alpha feature, so signing up with a separate identity is the
> self-service route.
> <https://tailscale.com/kb/1509/multiple-tailnets>

## 2. MagicDNS and HTTPS certificates

1. Admin console → **DNS**.
2. **MagicDNS** has been on by default for tailnets created since
   2022-10-20. If the page shows an **Enable MagicDNS** button, click it.
   <https://tailscale.com/kb/1081/magicdns>
3. Write down the **Tailnet DNS name** shown on this page (e.g.
   `tail1234.ts.net`). This is `<tailnet>` everywhere below.
4. On the same page, under **HTTPS Certificates**, click **Enable HTTPS**.
   Caddy uses this to serve `https://baja-vps.<tailnet>` with a valid
   certificate.
   <https://tailscale.com/kb/1153/enabling-https>

   ⚠ Certificates go into public Certificate Transparency logs, including
   each machine's full name. That is why hostnames are generic (`baja-w-0042`)
   and never a volunteer's name.

## 3. Device approval (recommended)

Admin console → **Settings** → **Device management** → turn on **Device
approval** (manually approve new devices).
<https://tailscale.com/kb/1099/device-approval>

With this on, any device someone joins interactively waits in **Machines**
with a *Needs approval* badge. Workers aren't affected, because their keys
are pre-authorized (`preauthorized: true`). Turn this on **before** step 8,
so that the **Pre-approved** option appears when you generate keys by hand.

## 4. Define the tags and upload the policy

In Tailscale, tags are defined by the policy file: a tag exists once it
appears in `tagOwners`. Uploading `acl.hujson` defines all four tags.

1. Admin console → **Access controls**.
2. Switch to the **JSON editor** (the text editor, not the visual one).
3. Select everything, delete it, and paste the full contents of
   [`acl.hujson`](acl.hujson).
4. Click **Save**. Tailscale parses the file and runs its `tests` section.
   If a test fails, it refuses to save and names the failing assertion.

**Validating from a terminal (optional).** The `tailscale` CLI has no
`acl` command. Validation goes through the API endpoint
`POST /api/v2/tailnet/-/acl/validate`, which parses the file and runs its
tests without changing the live policy. You need an API access token:
Admin console → **Settings** → **Keys** → **Generate access token…**, with
a short expiry.

```sh
export TS_API_TOKEN='tskey-api-...'
curl -sS -X POST "https://api.tailscale.com/api/v2/tailnet/-/acl/validate" \
  -H "Authorization: Bearer ${TS_API_TOKEN}" \
  -H "Content-Type: application/hujson" \
  --data-binary @infra/tailnet/acl.hujson
```

An empty JSON object (`{}`) means the file is valid and all tests passed.
Otherwise the response lists the errors.
(Source: Tailscale OpenAPI schema, operation `validateAndTestPolicyFile`,
`https://api.tailscale.com/api/v2?outputOpenapiSchema=true`.) Revoke the
token afterwards (**Settings** → **Keys** → … → **Revoke**).

## 5. Check key-expiry defaults

Nothing to change. Tagged devices have node-key expiry **disabled by
default**, so the VPS, studio and workers never need to re-authenticate.
<https://tailscale.com/kb/1068/tags>

You will confirm this per machine in step 11.

## 6. Create the orchestrator's OAuth client

1. Open <https://login.tailscale.com/admin/settings/trust-credentials>
   (Admin console → **Settings** → **Trust credentials**).
2. Click **Credential** → **OAuth**.
   <https://tailscale.com/kb/1215/oauth-clients>
3. Description: `baja orchestrator enrollment`.
4. Scopes: under **Keys**, select **Auth Keys** with **Write** access. This
   is the `auth_keys` scope. Select **nothing else**.
   <https://tailscale.com/kb/1623/trust-credentials>
5. Tags: you must pick tags for an `auth_keys` client. Add **only**
   `tag:enroller`.
6. Click **Generate credential**.
7. Copy the **Client ID** and **Client secret** into your password manager
   now. The secret is shown only once and is case-sensitive.

## 7. Store the client secret for the orchestrator

The orchestrator reads three environment variables (spec:
[`mint_auth_key.md`](mint_auth_key.md)):

| Env var | Value |
| --- | --- |
| `TAILSCALE_OAUTH_CLIENT_ID` | Client ID from step 6 |
| `TAILSCALE_OAUTH_CLIENT_SECRET` | Client secret from step 6 |
| `TAILSCALE_TAILNET` | `-` (the token's own tailnet; this is the default) |

Put them in the orchestrator's env file on the VPS. That file is
**never committed** and is readable by root only. For example:

```sh
sudo install -d -m 0700 /etc/baja
sudo install -m 0600 /dev/null /etc/baja/orchestrator.env
sudoedit /etc/baja/orchestrator.env
```

```ini
TAILSCALE_OAUTH_CLIENT_ID=k1a2b3c4d5CNTRL
TAILSCALE_OAUTH_CLIENT_SECRET=tskey-client-...
TAILSCALE_TAILNET=-
```

The compose file in `infra/` loads it with `env_file:`. Adjust the path if
that file uses a different one.

**Smoke test (optional, from any machine with curl).** Run the two curl
commands in `mint_auth_key.md`, then revoke the key you just created, so
an unused key is not left lying around:

```sh
curl -sS -X DELETE "https://api.tailscale.com/api/v2/tailnet/-/keys/<id from response>" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

A `200` from the keys call proves the scope, the tag ownership and the
secret are all correct.

## 8. Enroll the VPS as `tag:server` (by hand, once)

**8a. Generate a one-off key.** Admin console → **Settings** → **Keys** →
**Generate auth key…**
<https://tailscale.com/kb/1085/auth-keys>

* Description: `baja-vps`
* Reusable: **off**
* Expiration: **1 day**
* Ephemeral: **off**
* Tags: **on**, then add `tag:server`
* Pre-approved: **on** (appears only if step 3 is done)
* Click **Generate key** and copy `tskey-auth-…`

**8b. Install Tailscale on the VPS.** These commands assume Ubuntu 24.04
(noble). For another release, replace `noble` with its codename from
<https://pkgs.tailscale.com/stable/#linux>.

```sh
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update
sudo apt-get install -y tailscale
```

**8c. Join.**

```sh
sudo tailscale up --auth-key='tskey-auth-PASTE' --hostname=baja-vps
```

<https://tailscale.com/kb/1241/tailscale-up>

The key already carries `tag:server`, so you don't need `--advertise-tags`.

**8d. Let Caddy fetch the `*.ts.net` certificate.** Caddy gets the
certificate from the local `tailscaled`, with no Caddyfile config beyond
the site name `baja-vps.<tailnet>`.
<https://tailscale.com/kb/1190/caddy-certificates>

If Caddy runs on the host as the non-root `caddy` user:

```sh
echo 'TS_PERMIT_CERT_UID=caddy' | sudo tee -a /etc/default/tailscaled
sudo systemctl restart tailscaled
```

If Caddy runs in a container, mount the host's
`/var/run/tailscale/tailscaled.sock` into it. That belongs to the
Caddy/compose work in `infra/`, not this folder.

## 9. Enroll the streaming PC as `tag:studio` (by hand, first time)

**9a. Generate a key** exactly as in 8a, but with description
`baja-studio` and tag `tag:studio`.

**9b. Install Tailscale.** Download the amd64 MSI from
<https://pkgs.tailscale.com/stable/#windows>. In an **elevated**
PowerShell, in the download folder:

```powershell
msiexec.exe /i .\tailscale-setup-1.104.2-amd64.msi /quiet /norestart TS_UNATTENDEDMODE=always TS_NOLAUNCH=1 TS_ALLOWINCOMINGCONNECTIONS=always
```

Change the file name to the version you downloaded. The properties are
explained in [`worker_join.md`](worker_join.md#1-silent-tailscale-install).
<https://tailscale.com/kb/1189/install-windows-msi>

**9c. Join** (same elevated PowerShell):

```powershell
& "C:\Program Files\Tailscale\tailscale.exe" up --auth-key=tskey-auth-PASTE --hostname=baja-studio --unattended
```

**9d. Open the receiver port** to tailnet peers only:

```powershell
netsh advfirewall firewall add rule name="Baja studio receiver" dir=in action=allow protocol=TCP localport=8720 remoteip=100.64.0.0/10
```

**The master desktop** joins through the normal installer like any other
worker (`worker_join.md`), with an enrollment token from the orchestrator.
If it already runs Tailscale on your personal tailnet, read
"Volunteer/master PC already uses Tailscale" below first.

## 10. Lock down

* Admin console → **Machines**: only `baja-vps` and `baja-studio` should be
  listed, each with its tag.
* Revoke any keys from 8a/9a that were not used: **Settings** → **Keys**.
  Used one-off keys become invalid by themselves.

## 11. Verify

**On the VPS:**

```sh
tailscale status
```

The output should list `baja-vps` (self) and `baja-studio`. Tagged
machines show `tagged-devices` as their owner.

```sh
tailscale ping baja-studio
```

You should get a `pong from baja-studio (100.x.y.z) via …` line.
`tailscale ping` checks reachability at the Tailscale level. It does not
prove the ACL allows a port.
<https://tailscale.com/kb/1080/cli>

```sh
tailscale status --json | grep -E '"(DNSName|Tags)"'
```

`Self.DNSName` should be `baja-vps.<tailnet>.` and the tags
`["tag:server"]`.

**Check the ACL with real traffic.** These need the services running, so
come back to them once the orchestrator and studio receiver are deployed.

* VPS → studio must be **blocked**. On the VPS:

  ```sh
  nc -vz -w 5 baja-studio 8720
  ```

  This should time out. If it connects, the policy isn't the one in this
  folder.

* Studio → VPS must **work**. On the streaming PC:

  ```powershell
  Test-NetConnection baja-vps.<tailnet> -Port 443
  ```

  This should show `TcpTestSucceeded : True`.

* Once a worker has joined, on that worker:

  ```powershell
  Test-NetConnection baja-studio.<tailnet> -Port 8720
  ```

  This should be `True`.

  ```powershell
  Test-NetConnection baja-vps.<tailnet> -Port 22
  ```

  This should be `False`.

**In the admin console:** **Machines** → each tagged machine shows its tag
and **Expiry disabled**.

---

## Troubleshooting

### Key expiry on tagged nodes

* Tagged devices have node-key expiry **disabled by default**.
  <https://tailscale.com/kb/1068/tags>

  A machine shows an expiry date if it first logged in **as a user** and
  was tagged later. Fix it in **Machines** → **⋯** → **Disable key
  expiry**, or re-join it with a tagged key.
* Don't confuse the **auth key's** expiry with the **device's** node-key
  expiry. The auth key expires after an hour (orchestrator) or a day (by
  hand). A device that already joined keeps working after its auth key
  expires or is revoked.
  <https://tailscale.com/kb/1085/auth-keys>

### Pre-approval / "Needs approval"

* A worker stuck in **Machines** with *Needs approval* joined with a key
  that wasn't pre-authorized. Approve it with **⋯** → **Approve**; it
  connects immediately. Then check that the orchestrator sends
  `"preauthorized": true` (`mint_auth_key.md`).
  <https://tailscale.com/kb/1099/device-approval>
* The **Pre-approved** toggle is missing from **Generate auth key**: device
  approval (step 3) is off, so approval isn't needed at all.

### What a non-ephemeral tagged key means for volunteers' PCs

* **Non-ephemeral** means the device stays in the tailnet while it is
  offline. When the volunteer switches the PC on days later, it reconnects
  by itself with the same `100.x` IP, the same `baja-w-…` name and the same
  holder identity in the orchestrator. It does not need a new enrollment
  token. An **ephemeral** device would be removed automatically some time
  after going offline. A master or holder that went home for the night
  would then come back as a stranger, breaking holder bookkeeping.
  <https://tailscale.com/kb/1111/ephemeral-nodes>
* The device is owned by the tailnet, not by the volunteer. The volunteer
  gets no Tailscale account, can't see the admin console, and their other
  devices can't reach the tailnet. With this ACL, the PC accepts tailnet
  connections only on 8710, and only from other workers.
* **Offline machines pile up** in **Machines** after the event. They are
  harmless: no traffic, and expiry is off. Clean them up after the season:
  **Machines** → **⋯** → **Remove…**, or uninstall the worker (it runs
  `tailscale logout`).
* **To cut off a volunteer** (lost laptop, left the team), remove the
  machine in **Machines** and revoke its worker token in the orchestrator.
  Revoking the auth key does nothing: it is single-use and already spent.

### Volunteer/master PC already uses Tailscale

Windows runs one tailnet at a time. Joining Baja disconnects the PC from
its owner's personal tailnet, and switching back takes the worker offline.
The installer asks before switching (`worker_join.md`, step 1). On the
master desktop, stay on the Baja tailnet for the whole event.

### Orchestrator gets `403` minting a key

* The OAuth client lacks the `auth_keys` scope (re-create it as in step 6).
* The requested tag isn't owned by `tag:enroller`. Check `tagOwners` in
  the live policy against `acl.hujson`.
* The client was created with a tag other than `tag:enroller`. You can't
  change a client's tags after creation, so create a new client and update
  the env file.

### `tailscale up` fails on a worker

* `invalid key` / `key expired`: the key was already used, or more than an
  hour passed. Get a new enrollment token; the orchestrator mints a fresh
  key.
* `requested tags … are invalid or not permitted`: don't pass
  `--advertise-tags`, because the key's own tags apply. If the message
  persists, the policy's `tagOwners` don't match `acl.hujson`.
* Hangs and then times out: the Tailscale service isn't running. Run
  `sc query Tailscale`, then `sc start Tailscale`.

### Peers can't reach a worker's file server

* `tailscale ping baja-w-0042` works, but the fetch times out: the Windows
  firewall rule for 8710 is missing (`worker_join.md`, "Windows firewall"),
  or the file server is bound to `127.0.0.1` instead of the tailnet IP.
* `tailscale ping` itself fails or stays `via DERP`: run `tailscale
  netcheck` on both ends. Strict NATs work, just more slowly, through
  Tailscale's relays.
  <https://tailscale.com/kb/1080/cli>
