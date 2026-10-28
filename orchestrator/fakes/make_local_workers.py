"""Dev tool: create a master, a worker and a studio config for a local run.

Mints one enrollment token per role from a running orchestrator in dev
mode and writes a worker.json for each, with ports that do not collide on
one PC. Then start each with `python -m worker --config <folder>/worker.json`.

    uv run python orchestrator/fakes/make_local_workers.py
    uv run python orchestrator/fakes/make_local_workers.py --root .scratch/local \\
        --orchestrator http://127.0.0.1:8000 --admin-token dev-admin

The studio's clips land in <root>/studio/box/<prova>/<car>/<passagem>.mp4,
next to latest.m3u (worker/README.md, "Folder layout").
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

# role → (folder name, extra worker.json keys). Ports: master keeps the
# defaults, the others move up by one so all three fit on one machine.
LOCAL_FLEET = {
    "master": ("master", {}),
    "worker": ("worker", {"file_server_port": 8711, "setup_page_port": 8701}),
    "studio": ("studio", {"receiver_port": 8720, "setup_page_port": 8702}),
}
# Small enough to be honest about a laptop, large enough for test clips.
LOCAL_STORAGE_CAP_BYTES = 20_000_000_000


def mint_enrollment_token(client: httpx.Client, admin_token: str, role: str) -> str:
    response = client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": f"local {role}", "role": role},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response.raise_for_status()
    return response.json()["token"]


def write_worker_config(folder: Path, orchestrator_url: str, token: str, extra: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    config = {
        "orchestrator_url": orchestrator_url,
        "enrollment_token": token,
        "worker_name": f"local-{folder.name}",
        # Peers and the studio are reached on this address (no Tailscale locally).
        "tailnet_address": "127.0.0.1",
        "storage_cap_bytes": LOCAL_STORAGE_CAP_BYTES,
        **extra,
    }
    path = folder / "worker.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(".scratch/local"))
    parser.add_argument("--orchestrator", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-token", default="dev-admin")
    arguments = parser.parse_args()

    with httpx.Client(base_url=arguments.orchestrator, timeout=10) as client:
        for role, (folder_name, extra) in LOCAL_FLEET.items():
            folder = arguments.root / folder_name
            if (folder / "worker.json").exists():
                print(f"{folder / 'worker.json'} exists; keeping it (delete the folder to redo)")
                continue
            token = mint_enrollment_token(client, arguments.admin_token, role)
            path = write_worker_config(folder, arguments.orchestrator, token, extra)
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
