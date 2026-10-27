"""`BajaWorker.exe --leave-tailnet`: the uninstaller's `tailscale logout`.

worker_join.md "Uninstall": logging out expires this PC's node key, so it
can no longer use its Baja identity. Tailscale itself stays installed. The
logout happens only when the PC is on the Baja tailnet (it has a Baja tag),
so a volunteer's own tailnet is never touched. Always exits 0: a failed
logout must not block the uninstall (an admin can remove the machine from
the admin console at any time).
"""

from collections.abc import Callable

from baja_installer.tailscale_cli import TailscaleCli


def leave_baja_tailnet(tailscale: TailscaleCli, say: Callable[[str], None] = print) -> int:
    status = tailscale.status()
    if status is None or not status.on_baja_tailnet:
        say("not on the Baja tailnet; Tailscale left as it is")
        return 0
    finished = tailscale.logout()
    if finished.returncode == 0:
        say("logged out of the Baja tailnet")
    else:
        say(f"tailscale logout failed ({finished.returncode}): {finished.stderr.strip()}")
    return 0
