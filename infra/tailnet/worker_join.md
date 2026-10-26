# Worker first-run tailnet join (installer spec)

What the Windows installer and the worker's first run must do to put a
volunteer PC on the Baja tailnet as `tag:worker` (or the streaming PC as
`tag:studio`). It covers design §8 and §9. All commands run elevated,
either from the Inno Setup installer (already past UAC) or from the worker
service (NSSM, LocalSystem).

## Sequence

```
installer  ── 1. silent Tailscale MSI install
volunteer  ── 2. opens the localhost setup page, pastes the enrollment token
worker     ── 3. POST enrollment token to the orchestrator's public enroll endpoint
orchestr.  ── 4. mints the auth key (mint_auth_key.md), returns key + hostname + worker token
worker     ── 5. tailscale up --auth-key=... --hostname=...
worker     ── 6. reads its tailnet IP and MagicDNS name
worker     ── 7. registers with the orchestrator over the tailnet, using the worker token
```

> **Precondition for step 3.** The worker is not on the tailnet yet, so the
> enrollment exchange **cannot** go through the tailnet-only orchestrator
> API. Caddy must expose exactly that one endpoint publicly (e.g.
> `POST https://<public VPS name>/api/enroll`, rate-limited). Every other
> orchestrator route stays tailnet-only. This is acceptable because an
> enrollment token is single-use and is useless without the orchestrator's
> approval.

## 1. Silent Tailscale install

Bundle the current stable amd64 MSI from <https://pkgs.tailscale.com/stable/#windows>
(for example `tailscale-setup-1.104.2-amd64.msi`). Check it against the
published `.sha256` (append `.sha256` to the file URL) when building the
installer. Then run:

```bat
msiexec.exe /i "{tmp}\tailscale-setup-amd64.msi" /quiet /norestart ^
  TS_UNATTENDEDMODE=always ^
  TS_NOLAUNCH=1 ^
  TS_ALLOWINCOMINGCONNECTIONS=always ^
  TS_ONBOARDING_FLOW=hide
```

| Property | Value | Why |
| --- | --- | --- |
| `TS_UNATTENDEDMODE` | `always` | Tailscale keeps running when nobody is logged in to Windows. The worker is a service |
| `TS_NOLAUNCH` | `1` | Don't pop up the Tailscale GUI during a silent install |
| `TS_ALLOWINCOMINGCONNECTIONS` | `always` | Peers must reach this PC's file server on 8710. Stops the user from switching on "block incoming" |
| `TS_ONBOARDING_FLOW` | `hide` | No first-run Tailscale wizard; the worker does the login |

The MSI writes these as policies under
`HKLM\SOFTWARE\Policies\Tailscale`.
<https://tailscale.com/kb/1189/install-windows-msi>

The CLI ends up at `C:\Program Files\Tailscale\tailscale.exe` (default
`INSTALLDIR`). The rest of this document calls it `tailscale`.

**Already installed?** Many volunteers may already run Tailscale on their
own tailnet. Windows has one active tailnet at a time, so before step 5 run
`tailscale status --json`:

* `BackendState` is `"Running"` and `CurrentTailnet.MagicDNSSuffix` is the
  Baja tailnet's suffix → this PC has already joined. Skip to step 6.
* `BackendState` is `"Running"` on **another** tailnet → stop and show:
  "This PC is connected to another Tailscale network. Joining the Baja
  network will disconnect it. Continue?" Do not switch silently.
* Anything else (`NeedsLogin`, `Stopped`, …) → continue with step 5.

## 5. Join with the minted key

Write the key to a file only SYSTEM can read, so it never shows up in a
process list. `tailscale up` accepts `file:<path>`.
<https://tailscale.com/kb/1241/tailscale-up>

```bat
tailscale up ^
  --auth-key=file:C:\ProgramData\BajaWorker\ts-authkey.txt ^
  --hostname=baja-w-0042 ^
  --unattended ^
  --accept-routes=false ^
  --timeout=120s
```

Delete `ts-authkey.txt` as soon as the command returns, whether it
succeeded or failed. The key is single-use either way.

| Flag | Why |
| --- | --- |
| `--auth-key=file:…` | The single-use, pre-authorized, tagged key from the orchestrator. Its tags (`tag:worker` or `tag:studio`) are applied automatically. Don't pass `--advertise-tags` |
| `--hostname=baja-w-<enrollment id>` | A stable, non-sensitive name chosen by the orchestrator. The PC's own name may contain the volunteer's name, and with HTTPS on, hostnames go into public Certificate Transparency logs. The studio uses `baja-studio` |
| `--unattended` | (Windows) keep the connection up after the user logs out. This duplicates `TS_UNATTENDEDMODE=always`, and is harmless |
| `--accept-routes=false` | Nobody advertises subnet routes in this tailnet. Make sure a volunteer's LAN is never rerouted |
| `--timeout=120s` | Fail instead of blocking forever if the service isn't ready (the default `0s` waits forever) |

Exit code `0` means the device is up. On failure, show stderr on the setup
page and ask the volunteer for a **new** enrollment token, because the auth
key may already be spent. Common messages are in the README troubleshooting
section.

## 6. Learn the tailnet IP and MagicDNS name

Don't assume the hostname you asked for is the one you got. If the name is
taken, Tailscale changes it (e.g. `baja-w-0042-1`). Always read it back:

```bat
tailscale status --json
```

Fields to use (`ipnstate.Status`; the JSON keys are the Go field names,
see
<https://github.com/tailscale/tailscale/blob/main/ipn/ipnstate/ipnstate.go>):

| JSON path | Example | Use |
| --- | --- | --- |
| `BackendState` | `"Running"` | Must be `Running` before you register |
| `Self.TailscaleIPs[]` | `["100.101.102.103", "fd7a:115c:a1e0::1234:5678"]` | Take the entry without `:` as the IPv4 address. Bind the file server (8710) or receiver (8720) to it |
| `Self.DNSName` | `"baja-w-0042.tail1234.ts.net."` | MagicDNS FQDN. **Strip the trailing dot** before storing it |
| `Self.HostName` | `"baja-w-0042"` | The OS-reported name. Display only |
| `Self.Tags` | `["tag:worker"]` | Sanity check: must contain the expected tag |
| `CurrentTailnet.MagicDNSSuffix` | `"tail1234.ts.net"` | Lets you detect "already on Baja tailnet" on re-runs |

Tailscale warns that the `status --json` format may change, so parse it
defensively and fail clearly if a field is missing.
<https://tailscale.com/kb/1080/cli>

As a shortcut for the IP alone, `tailscale ip --4` prints the IPv4 address
on one line.

## 7. Register

`POST` to the orchestrator over the tailnet:
`https://baja-vps.<tailnet>.ts.net/...`, with `Authorization: Bearer
<worker_token>`. Send `tailnet_ip` (IPv4 from step 6) and `tailnet_name`
(DNS name without the trailing dot). Peers and the orchestrator use
`tailnet_name:8710` (or `:8720` for studio) as this worker's file
server address. The exact schema belongs to the API contract in `shared/`.

Re-register on every service start. Tagged devices keep their IP, but the
DNS name can change if an admin renames the device.

## Windows firewall

Tailscale's ACLs decide which tailnet peers may connect. The Windows
Defender firewall still applies to the Tailscale adapter, so the installer
must open the listening port for the tailnet range (`100.64.0.0/10`) only:

```bat
netsh advfirewall firewall add rule name="Baja worker file server" ^
  dir=in action=allow protocol=TCP localport=8710 remoteip=100.64.0.0/10
```

The studio role opens `localport=8720` instead, with the name `Baja studio
receiver`. Remove both rules in the uninstaller.

## Uninstall

Before it removes the worker, the uninstaller should run `tailscale logout`.
That disconnects the PC and expires its node key, so the PC can no longer
use its Baja identity. It should leave Tailscale itself installed if it was
present before Baja's installer ran. An admin then deletes any leftover
entry from the Machines page, which they can also do at any time without
touching the PC.
<https://tailscale.com/kb/1080/cli>
