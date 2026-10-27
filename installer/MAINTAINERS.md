# Windows worker installer: maintainer notes

How `BajaWorker-Setup-<version>.exe` is built, how to update what it
bundles, and what it does on a volunteer's PC. Volunteer docs (pt-BR):
[README.md](README.md). Design: `docs/design.md` §8, §9;
`infra/tailnet/worker_join.md`.

## Files

| Path | What |
| --- | --- |
| `VERSION` | `MAJOR.MINOR.PATCH` of the installer. A release tag must be `v<VERSION>` |
| `vendor.lock` | URL + SHA-256 of ffmpeg (BtbN), NSSM 2.24, Tailscale MSI |
| `fetch_vendor.py` | Downloads the lock's payloads into `vendor/` (gitignored), verifies hashes, unpacks zips. Stdlib only |
| `build.py` | Version stamp → PyInstaller onedir (`dist/BajaWorker/`) → copies ffmpeg + nssm in → optionally ISCC (`output/`) |
| `entry_worker.py` | PyInstaller entry script; calls `baja_installer.cli.main` |
| `baja_installer/` | Python frozen into `BajaWorker.exe` next to `worker` and `shared`: the commands below |
| `BajaWorker.iss` | Inno Setup 6 script (UTF-8 **with BOM**, or the Portuguese text breaks) |
| `ci/smoke_install.ps1` | CI: silent install → checks → reinstall → uninstall → checks |
| `../.github/workflows/installer.yml` | Linux tests + frozen build, then the Windows build, smoke test, artifact, release |
| `../tests/installer/` | Linux tests for all of the above |

`BajaWorker.exe` commands (`baja_installer/cli.py`):

| Command | Used by | Does |
| --- | --- | --- |
| *(none)* `[--config P] [--console]` | you, debugging | `python -m worker` |
| `--service --config P` | NSSM | waits (≤ 180 s) for `tailnet_address` to exist on the PC, then runs the worker; exit **75** if it never appears |
| `--first-run [--config P]` | Setup's last page, Start menu "concluir conexão" | opens the setup page, waits for enrollment, `tailscale up`, deletes the key, updates `worker.json`, restarts the service |
| `--init-config P` | Setup, every install/upgrade | sets `ffmpeg_path` and `enroll_url` in `worker.json`, keeps everything else |
| `--leave-tailnet` | Uninstaller | `tailscale logout`, only if the PC carries `tag:worker`/`tag:studio` |
| `--version`, `--check` | CI, support | version; import every bundled module + run bundled ffmpeg/ffprobe (exit 0 = good) |

## Building

### Locally on Linux (no Windows bits, but the same freeze)

```
uv sync --group installer
uv run pytest tests/installer
uv run python installer/build.py --ffmpeg-from-path
installer/dist/BajaWorker/BajaWorker --check
```

### The real installer

Only on Windows (CI does this): `Actions → Windows installer → Run
workflow` (optionally with an enroll URL), or push a tag. By hand on a
Windows box:

```
uv sync --group installer
uv run python installer/fetch_vendor.py
choco install innosetup -y
uv run python installer/build.py --compile-installer --enroll-url https://<PIPELINE_DOMAIN>
```

`--enroll-url` is the public 443 site that routes `POST /enroll`
(`https://$PIPELINE_DOMAIN`, `infra/vps/.env`). It is stamped into the exe
and written into `worker.json` at install time. CI takes it from the
workflow input or the repository variable **`BVP_ENROLL_URL`**; tag builds
fail without it. `orchestrator_url` is not baked in: it comes from the
`/enroll` answer (`tailnet_base_url`) and `--first-run` writes it.

### Releasing

1. Bump `installer/VERSION`, merge to `main`.
2. `git tag v<VERSION> && git push origin v<VERSION>`.
3. The workflow builds, runs the install/uninstall smoke test and attaches
   `BajaWorker-Setup-<VERSION>.exe` + `SHA256SUMS.txt` to the release.

Non-tag builds are versioned `<VERSION>+<git describe>` so they never
claim to be a release.

## Bumping a vendored payload

Every payload is pinned by exact URL and SHA-256 in `vendor.lock`. To
change one, edit the URL **and** the hash, then let CI build it. Never
copy a hash from the same place you download the file from without a
second source when one exists.

**ffmpeg (BtbN).** Releases: <https://github.com/BtbN/FFmpeg-Builds/releases>.
Pick a dated `autobuild-YYYY-MM-DD-HH-MM` tag (never `latest`, which
moves daily) and the asset `ffmpeg-n<X.Y.Z>-…-win64-gpl-shared-<X.Y>.zip`
(release branch, not the `N-…` master build). GitHub shows a
`sha256:` digest per asset:

```
gh api repos/BtbN/FFmpeg-Builds/releases/tags/<tag> \
  --jq '.assets[] | select(.name|test("win64-gpl-shared")) | .name + " " + .digest'
```

Set `url`, `sha256` and `path_in_archive` (the zip's top folder, i.e. the
file name without `.zip`). **BtbN deletes old autobuild releases after a
while**, so an old pin eventually 404s. If that happens, bump; or, to keep
a known-good build, upload the zip to a release of this repo and point
`url` there (same hash).

**NSSM.** 2.24 (2014) is the last release; there is nothing to bump.
`nssm.cc` often answers 503 to CI runners, so the lock lists an
Internet Archive copy under `mirrors` (same SHA-256), and CI caches the
download (`installer/vendor/downloads`, keyed on `vendor.lock`).

**Mirrors.** Any entry may have `mirrors = ["https://…"]`. `fetch_vendor.py`
tries `url` three times, then each mirror, and keeps only bytes with the
pinned hash, so a mirror can never change what ships. Add one whenever an
upstream is unreliable or deletes old files (BtbN).

**Tailscale.** `curl -s 'https://pkgs.tailscale.com/stable/?mode=json' | jq .MSIs.amd64`
gives the current MSI name. Tailscale publishes the hash next to it:
`curl -s https://pkgs.tailscale.com/stable/<msi>.sha256`. Download it and
compare with `sha256sum` before committing. The installer only installs
Tailscale when `C:\Program Files\Tailscale\tailscale.exe` is missing; it
never upgrades an existing one (Tailscale updates itself).

**Licences.** The ffmpeg build is GPL; `build.py` ships BtbN's
`LICENSE.txt` in `ffmpeg\`. The GPL also requires offering the
corresponding source: BtbN's release page links the exact FFmpeg commit
(the `g<hash>` in the file name). NSSM is public domain. Tailscale's MSI is
installed as-is under its own licence.

## What happens on a volunteer's PC

### Install (one UAC prompt)

1. Files → `C:\Program Files\BajaWorker\` (`BajaWorker.exe`, `_internal\`,
   `ffmpeg\`, `nssm.exe`, `Baja Worker.url`).
2. Tailscale MSI, silent, with `TS_UNATTENDEDMODE=always TS_NOLAUNCH=1
   TS_ALLOWINCOMINGCONNECTIONS=always TS_ONBOARDING_FLOW=hide`, if
   `tailscale.exe` is missing. Log: `C:\BajaWorker\logs\tailscale-msi.log`.
3. `icacls C:\BajaWorker /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F
   *S-1-5-32-544:(OI)(CI)F`: SYSTEM + Administrators only, by SID so it
   works on a pt-BR Windows. `worker.json` holds the bearer token in
   plaintext (design §17).
4. `BajaWorker.exe --init-config C:\BajaWorker\worker.json`.
5. NSSM service `BajaWorker`: `BajaWorker.exe --service --config
   C:\BajaWorker\worker.json`, LocalSystem, auto-start, working directory
   `C:\BajaWorker`, stdout/stderr → `C:\BajaWorker\logs\service-std{out,err}.log`
   (rotated at 10 MB), restart 10 s after any exit, CTRL-C then 30 s grace
   on stop. SCM failure actions also restart nssm itself.
6. Firewall: inbound TCP 8710 (`Baja Worker file server (TCP 8710)`) and
   8720 (`Baja Worker studio receiver (TCP 8720)`), all profiles, remote
   addresses `100.64.0.0/10` (Tailscale) only. Both ports on every PC: the
   role is decided by the token after install.
7. After Finish: `BajaWorker.exe --first-run` (elevated, console window).

An upgrade (running a newer Setup) stops and removes the service, replaces
the files, re-runs 2–6 (rules are deleted before being re-added), and
keeps `worker.json`'s identity.

### First run

```
volunteer   pastes token, folder, cap on http://localhost:8700  → worker.json
service     POST /enroll → worker.json (id, token, role, tailnet_base_url)
                         + tailscale-authkey.txt next to it
--first-run sees both → tailscale status (other tailnet? ask) →
            tailscale up --auth-key=file:… --hostname=baja-w-<id8>|baja-studio
                         --unattended --accept-routes=false --timeout=120s --reset
            deletes tailscale-authkey.txt (always)
            worker.json: tailnet_address = Tailscale IPv4,
                         orchestrator_url = tailnet_base_url (if still the default)
            nssm restart BajaWorker
```

If `up` fails, the key is spent: `--first-run` offers to erase the
enrollment so the setup page asks for a new token.

### Every boot

1. Windows starts Tailscale and `BajaWorker` (auto-start; order not
   guaranteed).
2. `--service` reads `worker.json`. Before enrollment it starts the worker
   at once (setup page only). After enrollment it waits until
   `tailnet_address` is assigned to a local adapter (Tailscale up), up to
   180 s; if not, it exits 75 and NSSM retries 10 s later, forever.
3. The worker starts: setup/status page on `127.0.0.1:8700`, file server
   (8710) or studio receiver (8720) bound to the Tailscale IP, then
   heartbeats to `orchestrator_url` every 15 s (worker/README.md).
4. Logs: `<working folder>\logs\worker.log` (the worker's own), plus
   `C:\BajaWorker\logs\service-*.log` (anything printed to the console,
   e.g. the `--service` wait and Python tracebacks).

### Uninstall

Stops and removes the service, deletes both firewall rules, runs
`--leave-tailnet`, removes `C:\Program Files\BajaWorker`, and tells the
volunteer that `C:\BajaWorker` (and a custom working folder) is kept.
Tailscale stays installed. The Machines entry in the Tailscale admin
console can be deleted at any time.

## Debugging on a PC

* Service state: `sc query BajaWorker`, `"C:\Program Files\BajaWorker\nssm.exe" status BajaWorker`.
* Run in the foreground (stop the service first, elevated prompt):
  `"C:\Program Files\BajaWorker\BajaWorker.exe" --config C:\BajaWorker\worker.json --console`.
* Build health: `BajaWorker.exe --check`.
* Tailscale: `"C:\Program Files\Tailscale\tailscale.exe" status`.
