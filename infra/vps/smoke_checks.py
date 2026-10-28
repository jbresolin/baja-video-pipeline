#!/usr/bin/env python3
"""Decision logic for compose-smoke.sh: turn raw command output into PASS/FAIL.

compose-smoke.sh runs the commands (docker, curl, stat, findmnt) and pipes
their output here; this file only reads text and decides. Keeping the
decisions in Python makes them unit-testable on any machine
(tests/infra/test_compose_smoke.py) with no Docker and no VPS.

Standard library only (Python 3.10+, stock on Ubuntu), plus the repo's own
shared/reviewers_file.py, which is also standard library only.

Every check prints one or more result lines, tab separated:

    PASS<TAB>check name<TAB>detail
    FAIL<TAB>check name<TAB>what is wrong and what to look at

`report` reads all collected lines and prints the table.
"""

import argparse
import ipaddress
import json
import sys
from pathlib import Path

# The repo's reviewers parser (same rules the orchestrator applies at start).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "src"))
from shared.reviewers_file import ReviewersFileError, parse_reviewers  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
GIB = 1024**3

# Services of docker-compose.yml (the pipeline stack only; Evolution and the
# public Caddy are the base stack's). pipeline-edge has no healthcheck, so
# "running" is the best it can say.
EXPECTED_SERVICES = ["orchestrator", "review-ui", "pipeline-edge"]

# The orchestrator's default when BVP_TRUSTED_PROXY_CIDRS is unset (config.py).
DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.1/32,10.201.0.0/24,172.16.0.0/12"

# Folders the orchestrator writes (design §15, README §2.1), and the two
# files it reads. All must belong to the containers' user (design §17).
DATA_FOLDERS = ["", "inbox", "proxies", "thumbs", "outbox", "db"]
DATA_FILES = ["reviewers.txt", "teams.csv"]


def result(status: str, check: str, detail: str) -> str:
    # Tabs and newlines inside a detail would break the table.
    clean = " ".join(detail.split())
    return f"{status}\t{check}\t{clean}"


# --- The base stack (jbresolin/baja-vps, README §1) ----------------------------


def check_base_network(inspect_output: str, network: str, trusted_cidrs: str) -> list[str]:
    """`docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}'`, or MISSING.

    Two rows: the network exists, and BVP_TRUSTED_PROXY_CIDRS covers every
    subnet of it. The base Caddy reaches the orchestrator over this network,
    so its address is the TCP peer of every public /enroll; if that address
    is not trusted, the orchestrator ignores X-Forwarded-For and every
    volunteer shares the base Caddy's single rate-limit bucket (README §9).
    """
    check = f"base network {network}"
    if inspect_output.strip() in ("", "MISSING"):
        return [result(FAIL, check, "not found; start the base stack first")]
    subnets = inspect_output.split()
    lines = [result(PASS, check, " ".join(subnets))]
    check = f"trusted proxies cover {network}"
    try:
        trusted = [ipaddress.ip_network(cidr.strip()) for cidr in trusted_cidrs.split(",")]
        networks = [ipaddress.ip_network(subnet) for subnet in subnets]
    except ValueError as error:
        return lines + [result(FAIL, check, str(error))]
    uncovered = [
        str(subnet)
        for subnet in networks
        if not any(subnet.version == cidr.version and subnet.subnet_of(cidr) for cidr in trusted)
    ]
    if uncovered:
        detail = f"{', '.join(uncovered)} not in BVP_TRUSTED_PROXY_CIDRS={trusted_cidrs}; README §9"
        return lines + [result(FAIL, check, detail)]
    return lines + [result(PASS, check, trusted_cidrs)]


def check_base_caddy(inspect_output: str, container: str) -> list[str]:
    """`docker inspect -f '{{.State.Status}}' caddy`, or MISSING."""
    check = f"base container {container}"
    status = inspect_output.strip() or "MISSING"
    if status == "MISSING":
        return [result(FAIL, check, "not found; start the base stack first")]
    if status != "running":
        return [result(FAIL, check, f"state {status}; the base stack's docker compose logs caddy")]
    return [result(PASS, check, "running")]


def check_confd(path: Path, domain: str) -> list[str]:
    """up.sh installed the public routes for this domain into the base Caddy."""
    check = "caddy routes installed"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [result(FAIL, check, f"{path} missing; run ./up.sh")]
    if f"\n{domain} {{" not in text:
        return [result(FAIL, check, f"{path} is not for {domain}; run ./up.sh again")]
    return [result(PASS, check, str(path))]


# --- Containers ---------------------------------------------------------------


def parse_compose_ps(text: str) -> list[dict]:
    """`docker compose ps --format json` output → list of container dicts.

    Compose ≥ 2.21 prints one JSON object per line; older versions print a
    single JSON array. Both are accepted.
    """
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def check_containers(ps_output: str, expected: list[str]) -> list[str]:
    try:
        containers = parse_compose_ps(ps_output)
    except json.JSONDecodeError as error:
        return [result(FAIL, "containers", f"cannot read docker compose ps output: {error}")]
    by_service = {container.get("Service"): container for container in containers}
    lines = []
    for service in expected:
        check = f"container {service}"
        container = by_service.get(service)
        if container is None:
            lines.append(result(FAIL, check, "not created; docker compose up -d it"))
            continue
        state = container.get("State", "?")
        health = container.get("Health", "")
        if state != "running":
            lines.append(result(FAIL, check, f"state {state}; see docker compose logs {service}"))
        elif health and health != "healthy":
            lines.append(result(FAIL, check, f"running but {health}; see its logs"))
        else:
            lines.append(result(PASS, check, health or "running (no healthcheck)"))
    return lines


# --- HTTP answers -------------------------------------------------------------


def check_http(
    check: str, got_status: str, body: str, expected_status: int, json_field: str | None
) -> list[str]:
    """`curl -w %{http_code}` gives "000" when it could not connect at all."""
    if got_status == "000":
        return [result(FAIL, check, "no connection (DNS, firewall, Caddy down, wrong IP?)")]
    if got_status != str(expected_status):
        return [result(FAIL, check, f"HTTP {got_status}, expected {expected_status}")]
    if json_field:
        key, _, wanted = json_field.partition("=")
        try:
            value = json.loads(body).get(key)
        except (json.JSONDecodeError, AttributeError):
            return [result(FAIL, check, f"HTTP {got_status} but the body is not JSON")]
        if str(value) != wanted:
            return [result(FAIL, check, f"{key} is {value!r}, expected {wanted!r}")]
        return [result(PASS, check, f"HTTP {got_status}, {key}={wanted}")]
    return [result(PASS, check, f"HTTP {got_status}")]


def check_evolution_state(got_status: str, body: str) -> list[str]:
    """GET /instance/connectionState/{instance} → {"instance": {"state": "open"}}."""
    check = "evolution instance state"
    if got_status != "200":
        return [result(FAIL, check, f"HTTP {got_status} (apikey or instance name wrong?)")]
    try:
        state = json.loads(body)["instance"]["state"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [result(FAIL, check, "unexpected answer from Evolution")]
    if state != "open":
        detail = f"state {state!r}; the base stack's runbook (do NOT scan a new QR first)"
        return [result(FAIL, check, detail)]
    return [result(PASS, check, "open")]


def check_webhook(got_status: str, body: str, url: str) -> list[str]:
    """GET /webhook/find/{instance}: enabled, and pointing at the orchestrator."""
    check = "evolution webhook"
    if got_status != "200":
        return [result(FAIL, check, f"HTTP {got_status} from webhook/find")]
    try:
        found = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        return [result(FAIL, check, "unexpected answer from Evolution")]
    if isinstance(found, dict) and isinstance(found.get("webhook"), dict):
        found = found["webhook"]
    if not isinstance(found, dict) or not found.get("enabled"):
        return [result(FAIL, check, "disabled; run ./up.sh (README §8)")]
    if found.get("url") != url:
        return [result(FAIL, check, f"points at {found.get('url')!r}; run ./up.sh")]
    return [result(PASS, check, f"enabled, {url}")]


def check_metrics(got_status: str, body: str) -> list[str]:
    check = "orchestrator /metrics"
    if got_status != "200":
        return [result(FAIL, check, f"HTTP {got_status} (BVP_ADMIN_TOKEN wrong?)")]
    if "bvp_jobs{" not in body:
        return [result(FAIL, check, "answered, but without bvp_jobs; old orchestrator image?")]
    online = [line for line in body.splitlines() if line.startswith("bvp_workers_online{")]
    total = sum(int(float(line.rsplit(" ", 1)[1])) for line in online)
    return [result(PASS, check, f"ok, {total} worker(s) online")]


# --- Files on disk ------------------------------------------------------------


def check_reviewers_file(path: Path) -> list[str]:
    check = "reviewers file"
    try:
        reviewers = parse_reviewers(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [result(FAIL, check, f"{path} does not exist (README §4)")]
    except PermissionError:
        return [result(FAIL, check, f"cannot read {path}; run the smoke test with sudo")]
    except ReviewersFileError as error:
        return [result(FAIL, check, str(error))]
    if not reviewers:
        return [result(FAIL, check, "no reviewers; Caddy refuses to load an empty file")]
    return [result(PASS, check, f"{len(reviewers)} reviewer(s): {', '.join(sorted(reviewers))}")]


def check_ownership(stat_output: str, expected_uid: int, expected_gid: int) -> list[str]:
    """Lines of `stat -c '%u %g %a %n'`, or `MISSING <path>` written by the shell."""
    lines = []
    for raw in stat_output.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("MISSING "):
            path = raw.removeprefix("MISSING ")
            lines.append(result(FAIL, f"owner {path}", "missing (README §2.1)"))
            continue
        uid, gid, mode, path = raw.split(" ", 3)
        if int(uid) != expected_uid or int(gid) != expected_gid:
            fix = f"sudo chown {expected_uid}:{expected_gid} {path}"
            detail = f"{uid}:{gid}, expected {expected_uid}:{expected_gid}; fix: {fix}"
            lines.append(result(FAIL, f"owner {path}", detail))
        else:
            lines.append(result(PASS, f"owner {path}", f"{uid}:{gid} mode {mode}"))
    return lines


def check_inbox_mount(findmnt_output: str, cap_gb: float) -> list[str]:
    """`findmnt -n -b -o FSTYPE,SIZE <inbox>`: empty when it is not a mount point.

    The inbox must be its own filesystem, so a full inbox cannot fill the
    root disk (README §2.3). The runbook makes it `BVP_INBOX_CAP_GB` GiB; a
    bigger one would let the inbox outgrow what the disk budget assumes.
    """
    check = "inbox filesystem"
    fields = findmnt_output.split()
    if len(fields) < 2:
        return [result(FAIL, check, "not a separate mount; README §2.3 (25 GiB loop file)")]
    fstype, size_bytes = fields[0], int(fields[1])
    size_gib = size_bytes / GIB
    # A little slack for rounding; ext4 reports slightly less than the file size.
    if size_gib > cap_gb * 1.05:
        return [result(FAIL, check, f"{fstype} {size_gib:.1f} GiB, bigger than the {cap_gb} cap")]
    return [result(PASS, check, f"{fstype}, {size_gib:.1f} GiB (cap {cap_gb})")]


# --- The table ----------------------------------------------------------------


def render_report(result_lines: list[str]) -> tuple[str, bool]:
    """(the table, True if every check passed)."""
    rows = [line.split("\t", 2) for line in result_lines if line.strip()]
    rows = [row + [""] * (3 - len(row)) for row in rows]
    width = max([len(row[1]) for row in rows] + [len("CHECK")])
    out = [f"{'RESULT':<6}  {'CHECK':<{width}}  DETAIL", f"{'-' * 6}  {'-' * width}  {'-' * 6}"]
    out += [f"{status:<6}  {check:<{width}}  {detail}" for status, check, detail in rows]
    failures = sum(1 for row in rows if row[0] != PASS)
    all_passed = failures == 0 and bool(rows)
    out.append("")
    out.append("ALL CHECKS PASSED" if all_passed else f"{failures} CHECK(S) FAILED")
    return "\n".join(out), all_passed


# --- Command line -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="compose-smoke.sh helper")
    commands = parser.add_subparsers(dest="command", required=True)
    containers = commands.add_parser("containers", help="stdin: docker compose ps --format json")
    containers.add_argument("services", nargs="*", default=EXPECTED_SERVICES)
    http = commands.add_parser("http", help="stdin: response body")
    http.add_argument("check")
    http.add_argument("--got", required=True, help="curl's %%{http_code}")
    http.add_argument("--expect", type=int, required=True)
    http.add_argument("--json-field", help="key=value the JSON body must contain")
    for name in ("evolution-state", "metrics"):
        sub = commands.add_parser(name, help="stdin: response body")
        sub.add_argument("--got", required=True)
    webhook = commands.add_parser("webhook", help="stdin: GET /webhook/find body")
    webhook.add_argument("--got", required=True)
    webhook.add_argument("--url", required=True)
    base_network = commands.add_parser("base-network", help="stdin: subnets, or MISSING")
    base_network.add_argument("network")
    base_network.add_argument("--trusted-cidrs", default=DEFAULT_TRUSTED_PROXY_CIDRS)
    base_caddy = commands.add_parser("base-caddy", help="stdin: container status, or MISSING")
    base_caddy.add_argument("container")
    confd = commands.add_parser("confd")
    confd.add_argument("path", type=Path)
    confd.add_argument("--domain", required=True)
    reviewers = commands.add_parser("reviewers")
    reviewers.add_argument("path", type=Path)
    ownership = commands.add_parser("ownership", help="stdin: stat -c '%%u %%g %%a %%n' lines")
    ownership.add_argument("--uid", type=int, default=10001)
    ownership.add_argument("--gid", type=int, default=10001)
    inbox = commands.add_parser("inbox-mount", help="stdin: findmnt -n -b -o FSTYPE,SIZE")
    inbox.add_argument("--cap-gb", type=float, default=25)
    report = commands.add_parser("report", help="print the table; exit 1 on any FAIL")
    report.add_argument("results", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "report":
        table, all_passed = render_report(arguments.results.read_text().splitlines())
        print(table)
        return 0 if all_passed else 1

    stdin = "" if arguments.command in ("reviewers", "confd") else sys.stdin.read()
    if arguments.command == "base-network":
        lines = check_base_network(stdin, arguments.network, arguments.trusted_cidrs)
    elif arguments.command == "base-caddy":
        lines = check_base_caddy(stdin, arguments.container)
    elif arguments.command == "confd":
        lines = check_confd(arguments.path, arguments.domain)
    elif arguments.command == "webhook":
        lines = check_webhook(arguments.got, stdin, arguments.url)
    elif arguments.command == "containers":
        lines = check_containers(stdin, arguments.services)
    elif arguments.command == "http":
        lines = check_http(
            arguments.check, arguments.got, stdin, arguments.expect, arguments.json_field
        )
    elif arguments.command == "evolution-state":
        lines = check_evolution_state(arguments.got, stdin)
    elif arguments.command == "metrics":
        lines = check_metrics(arguments.got, stdin)
    elif arguments.command == "reviewers":
        lines = check_reviewers_file(arguments.path)
    elif arguments.command == "ownership":
        lines = check_ownership(stdin, arguments.uid, arguments.gid)
    else:
        lines = check_inbox_mount(stdin, arguments.cap_gb)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
