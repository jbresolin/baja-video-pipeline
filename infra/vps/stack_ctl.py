#!/usr/bin/env python3
"""Decision logic for up.sh and down.sh: what to do with the Evolution webhook.

up.sh and down.sh run the commands (docker, curl, envsubst); this file only
reads text and decides, so the decisions are unit-testable on any machine
(tests/infra/test_up_down.py). Standard library only (Python 3.10+, stock on
Ubuntu).

The webhook is the Baja Bot's Evolution INSTANCE webhook (README §8). up.sh
points it at the orchestrator with the shared secret; down.sh disables it,
so Evolution stops posting to a stack that is gone. Each decision prints one
line, tab separated:

    <action><TAB><what the operator should read>

Actions:
    up:   set | ok
    down: disable | none | foreign
"""

import argparse
import json
import os
import sys

# What the orchestrator consumes (docs/api.md "Evolution webhook").
WANTED_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
SECRET_HEADER = "X-Webhook-Secret"


def parse_find(text: str) -> dict:
    """`GET /webhook/find/{instance}` → the stored webhook, or {} if there is none.

    Evolution answers `null` (or an empty body) for an instance that never had
    a webhook. Field names differ between the stored row (webhookByEvents,
    webhookBase64) and the set body (byEvents, base64); both are accepted.
    """
    text = text.strip()
    if not text:
        return {}
    found = json.loads(text)
    if not isinstance(found, dict):
        return {}
    # Some versions wrap it like the set body does.
    if isinstance(found.get("webhook"), dict):
        found = found["webhook"]
    return found


def flag(webhook: dict, *names: str) -> bool:
    return any(bool(webhook.get(name)) for name in names)


def decide_up(found: dict, url: str, secret: str) -> tuple[str, str]:
    """Set the webhook unless it is already exactly what the orchestrator needs."""
    if not found:
        return "set", "no webhook yet; pointing it at the orchestrator"
    headers = found.get("headers") or {}
    problems = []
    if not found.get("enabled"):
        problems.append("disabled")
    if found.get("url") != url:
        problems.append(f"url is {found.get('url')!r}")
    if sorted(found.get("events") or []) != sorted(WANTED_EVENTS):
        problems.append("events differ")
    # base64 on would buffer every 2 GB document in Evolution's RAM (README §8).
    if flag(found, "base64", "webhookBase64"):
        problems.append("base64 is on")
    if flag(found, "byEvents", "webhookByEvents"):
        problems.append("byEvents is on")
    if not isinstance(headers, dict) or headers.get(SECRET_HEADER) != secret:
        problems.append(f"{SECRET_HEADER} missing or different")
    if problems:
        return "set", "updating: " + ", ".join(problems)
    return "ok", "already points at the orchestrator"


def decide_down(found: dict, url: str) -> tuple[str, str]:
    """Disable our webhook; never touch one that someone pointed elsewhere."""
    if not found or not found.get("enabled"):
        return "none", "already disabled"
    if found.get("url") != url:
        return "foreign", f"enabled but points at {found.get('url')!r}, not ours; left alone"
    return "disable", "disabling, so Evolution stops posting to the stopped orchestrator"


def webhook_body(enabled: bool, url: str, secret: str) -> dict:
    """`POST /webhook/set/{instance}` body as v2.3.7 validates it (README §8).

    Nested under "webhook", with byEvents/base64. `url` is required even when
    disabled, so down.sh sends ours.
    """
    webhook = {
        "enabled": enabled,
        "url": url,
        "byEvents": False,
        "base64": False,
        "events": WANTED_EVENTS if enabled else [],
    }
    if enabled:
        webhook["headers"] = {SECRET_HEADER: secret}
    return {"webhook": webhook}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="up.sh / down.sh helper")
    commands = parser.add_subparsers(dest="command", required=True)
    action = commands.add_parser("webhook-action", help="stdin: GET /webhook/find body")
    action.add_argument("--mode", choices=["up", "down"], required=True)
    action.add_argument("--url", required=True)
    body = commands.add_parser("webhook-body", help="print the POST /webhook/set body")
    body.add_argument("--enabled", choices=["true", "false"], required=True)
    body.add_argument("--url", required=True)
    arguments = parser.parse_args(argv)

    # The secret comes from the environment, never argv (visible in `ps`).
    if arguments.command == "webhook-body":
        needs_secret = arguments.enabled == "true"
    else:
        needs_secret = arguments.mode == "up"
    secret = os.environ.get("BVP_WEBHOOK_SECRET", "")
    if needs_secret and not secret:
        print("stack_ctl: BVP_WEBHOOK_SECRET is not set", file=sys.stderr)
        return 2

    if arguments.command == "webhook-body":
        print(json.dumps(webhook_body(arguments.enabled == "true", arguments.url, secret)))
        return 0

    try:
        found = parse_find(sys.stdin.read())
    except json.JSONDecodeError:
        print("stack_ctl: Evolution's webhook answer is not JSON", file=sys.stderr)
        return 2
    if arguments.mode == "up":
        verdict, message = decide_up(found, arguments.url, secret)
    else:
        verdict, message = decide_down(found, arguments.url)
    print(f"{verdict}\t{message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
