"""Reading `tailscale status --json` and building `tailscale up`
(installer/baja_installer/tailscale_cli.py, infra/tailnet/worker_join.md).

The JSON samples below are trimmed real outputs: only the fields we read.
"""

import json
import subprocess
from pathlib import Path

from baja_installer.tailscale_cli import TailscaleCli, parse_status, up_arguments


def status_json(state="Running", tags=None, ips=None, dns="baja-w-1234abcd.tail1234.ts.net."):
    return json.dumps(
        {
            "BackendState": state,
            "Self": {
                "TailscaleIPs": ips if ips is not None else ["100.101.102.103", "fd7a:115c::1"],
                "DNSName": dns,
                "Tags": tags,
            },
            "CurrentTailnet": {"MagicDNSSuffix": "tail1234.ts.net"},
        }
    )


def test_a_running_baja_worker_is_recognised():
    status = parse_status(status_json(tags=["tag:worker"]))

    assert status.on_baja_tailnet is True
    # The IPv4 address is the one without ":" (the other one is IPv6).
    assert status.ipv4 == "100.101.102.103"
    # MagicDNS names end with a dot in the JSON; we store them without it.
    assert status.dns_name == "baja-w-1234abcd.tail1234.ts.net"


def test_the_studio_tag_counts_as_baja_too():
    assert parse_status(status_json(tags=["tag:studio"])).on_baja_tailnet is True


def test_a_volunteers_own_tailnet_is_not_baja():
    # Personal devices are owned by a user and have no tags at all.
    status = parse_status(status_json(tags=None))
    assert status.is_running is True
    assert status.on_baja_tailnet is False


def test_logged_out_is_neither_running_nor_baja():
    status = parse_status(status_json(state="NeedsLogin", tags=None, ips=[], dns=""))
    assert status.is_running is False
    assert status.on_baja_tailnet is False
    assert status.ipv4 is None
    assert status.dns_name is None


def test_unexpected_output_does_not_crash():
    # Tailscale says the format may change. Missing or odd fields must
    # give "unknown", never an exception.
    assert parse_status("not json") is None
    assert parse_status("[]") is None
    status = parse_status('{"Self": "weird", "BackendState": null}')
    assert status.backend_state == ""
    assert status.on_baja_tailnet is False


def test_up_reads_the_key_from_a_file_and_never_from_the_command_line():
    arguments = up_arguments(
        Path("C:/BajaWorker/tailscale-authkey.txt"), "baja-w-1", switching_tailnet=False
    )

    # "file:" keeps the secret key out of the process list (worker_join.md §5).
    assert "--auth-key=file:C:/BajaWorker/tailscale-authkey.txt" in arguments
    assert not any(a.startswith("--auth-key=tskey") for a in arguments)
    # The flags the join spec requires.
    for flag in ("--hostname=baja-w-1", "--unattended", "--accept-routes=false", "--timeout=120s"):
        assert flag in arguments
    # Tags come from the key; asking for them here would be refused.
    assert not any(a.startswith("--advertise-tags") for a in arguments)
    assert "--force-reauth" not in arguments


def test_leaving_another_tailnet_forces_a_new_login():
    arguments = up_arguments(Path("k.txt"), "baja-w-1", switching_tailnet=True)
    assert "--force-reauth" in arguments


def test_a_missing_tailscale_reads_as_no_status():
    # Before the MSI has run (or if it failed), there is no tailscale.exe.
    def missing_program(args):
        raise FileNotFoundError(args[0])

    cli = TailscaleCli("tailscale", run=missing_program)

    assert cli.status() is None
    # `up` reports the problem as a failed command instead of crashing.
    finished = cli.up(Path("k.txt"), "baja-w-1", switching_tailnet=False)
    assert finished.returncode != 0


def test_status_is_read_even_when_tailscale_exits_non_zero():
    # `tailscale status --json` may exit non-zero while logged out yet print JSON.
    def logged_out(args):
        return subprocess.CompletedProcess(args, 1, status_json(state="NeedsLogin"), "")

    assert TailscaleCli("tailscale", run=logged_out).status().backend_state == "NeedsLogin"
