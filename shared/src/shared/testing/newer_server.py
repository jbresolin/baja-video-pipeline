"""Pretend the orchestrator is newer than the client: every JSON answer gains fields.

Design §14: a client must never break because the server added a field.
Wrap the transport a client talks through in NewerServerTransport and
every JSON answer it reads (including error bodies) carries an extra field
at the top level and inside every nested object, the way a later contract
version might add one:

    {"job_id": "j1", "state": "done"}
    → {"job_id": "j1", "state": "done", "added_by_a_newer_server": {...}}

Lists are walked too, so each task, clip version or worker in a list gets
it. Non-JSON answers (video bytes, HTML pages) pass through untouched.
"""

import json
from typing import Any

import httpx

UNKNOWN_FIELD = "added_by_a_newer_server"
UNKNOWN_VALUE = {"meaning": "a field this client has never heard of", "since": "9.0.0"}

# Fields whose value is a free-form map (keys are data, not field names):
# an extra key there would be a new entry, not a new field. For example
# HeartbeatResponse.lease_status is {lease_id: status}.
FREE_FORM_MAPS = frozenset({"lease_status", "details", "format", "secrets", "jobs_by_state"})


def add_unknown_fields(value: Any) -> Any:
    """`value` (parsed JSON) with UNKNOWN_FIELD added to every object in it."""
    if isinstance(value, list):
        return [add_unknown_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    widened = {
        key: item if key in FREE_FORM_MAPS else add_unknown_fields(item)
        for key, item in value.items()
    }
    widened[UNKNOWN_FIELD] = UNKNOWN_VALUE
    return widened


class NewerServerTransport(httpx.BaseTransport):
    """An httpx transport that adds unknown fields to every JSON answer.

    `inner` is what really answers: another transport, or an httpx.Client
    (e.g. an orchestrator test harness's client).
    """

    def __init__(self, inner: httpx.BaseTransport | httpx.Client) -> None:
        self._send = inner.send if isinstance(inner, httpx.Client) else inner.handle_request
        # How many answers were widened, so a test can check it really happened.
        self.widened_answers = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._send(request)
        if "application/json" not in response.headers.get("content-type", ""):
            return response
        response.read()
        body = json.dumps(add_unknown_fields(response.json())).encode()
        self.widened_answers += 1
        headers = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in ("content-length", "content-encoding")
        ]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)
