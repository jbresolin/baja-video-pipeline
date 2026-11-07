"""A worker keeps working when the orchestrator answers with values it does not know.

What this is for: volunteers update their PCs later than the VPS. A newer
orchestrator may add a new job state, a new lease status or a new worker
role. test_newer_orchestrator.py proves that new *fields* are ignored;
these tests prove the same for new *values* in fields the worker already
reads, and that an answer the worker really cannot read becomes an ordinary
API error instead of a crash (seen 2026-11-06: a studio on 0.2.0 crashed
reading GET /jobs/{id}, never confirmed the delivery, and retried forever).

What these tests prove:
  * a heartbeat answer with an unknown lease status or role is read, the
    unknown values are left alone, and known ones still act;
  * a state-change answer (e.g. the outbox ACK) with an unknown state is read;
  * a job record the worker cannot read raises UnreadableAnswerError, which
    is an ApiError, so the callers' existing "could not look it up" paths run.

Not covered: the orchestrator side (it always sends values it knows).
"""

import httpx
import pytest

from shared.models import (
    HeartbeatRequest,
    JobState,
    LeaseHeartbeatStatus,
    OutboxAckRequest,
    WorkerRole,
)
from worker.api_client import ApiError, OrchestratorClient, UnreadableAnswerError

ORCHESTRATOR = "http://100.82.21.71:8080"
JOB_ID = "4cb84d39" + "0" * 24
SHA256_OF_THE_CLIP = "a" * 64


def client_answering(json_body: dict) -> OrchestratorClient:
    """An OrchestratorClient whose every request gets `json_body` back with 200."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json_body)

    http = httpx.Client(transport=httpx.MockTransport(answer))
    return OrchestratorClient(
        http, ORCHESTRATOR, worker_id="w-studio", bearer_token="placeholder-bearer-token"
    )


def a_studio_heartbeat() -> HeartbeatRequest:
    """What the studio sends every 15 s: its disk space and its two leases."""
    return HeartbeatRequest(
        free_bytes=107 * 1024**3,
        storage_cap_bytes=107 * 1024**3,
        current_lease_ids=["lease-a", "lease-b"],
    )


def test_a_heartbeat_answer_with_an_unknown_lease_status_is_still_read():
    # Arrange: two leases; the orchestrator says one is lost and uses a status
    # this worker version does not know for the other ("paused", say).
    client = client_answering(
        {
            "lease_status": {"lease-a": "lost", "lease-b": "paused"},
            "heartbeat_interval_s": 15,
            "accepting_new_raws": True,
        }
    )

    # Act: read the heartbeat answer.
    answer = client.heartbeat(a_studio_heartbeat())

    # Assert: the known status is the enum member the worker acts on; the
    # unknown one is kept as text and matches nothing the worker acts on.
    assert answer.lease_status["lease-a"] == LeaseHeartbeatStatus.LOST, (
        "a known status must still be recognised (the worker stops that task)"
    )
    assert answer.lease_status["lease-b"] == "paused", "an unknown status is kept as text"
    assert answer.lease_status["lease-b"] != LeaseHeartbeatStatus.LOST, (
        "an unknown status must never be mistaken for 'lost'"
    )


def test_a_heartbeat_answer_with_an_unknown_role_is_still_read():
    # Arrange: an orchestrator with a role this worker version does not know.
    client = client_answering(
        {"heartbeat_interval_s": 15, "accepting_new_raws": True, "role": "judge_station"}
    )

    # Act: read the heartbeat answer.
    answer = client.heartbeat(a_studio_heartbeat())

    # Assert: read, with the role as plain text, which is not one of the
    # roles a worker switches to by itself (worker.role_change ignores it).
    assert answer.role == "judge_station", "the unknown role is kept as text"
    assert answer.role not in (WorkerRole.MASTER, WorkerRole.WORKER), (
        "an unknown role must not look like master or worker"
    )


def test_an_outbox_ack_answer_with_an_unknown_state_is_still_read():
    # Arrange: the studio's ACK is accepted, and the job moves to a state
    # added in a later version ("on_air", say).
    client = client_answering({"job_id": JOB_ID, "state": "on_air"})
    ack = OutboxAckRequest(
        lease_id="lease-1", sha256=SHA256_OF_THE_CLIP, relative_path="suspensao/7/1.mp4"
    )

    # Act: send the ACK and read the answer.
    answer = client.outbox_ack(JOB_ID, ack)

    # Assert: no error. The ACK went through on the server, so failing here
    # would make the studio retry a delivery that already worked.
    assert answer.state == "on_air", "the new state is kept as text"


def test_a_known_state_is_still_the_enum_member():
    # Arrange: an answer with a state this worker knows.
    client = client_answering({"job_id": JOB_ID, "state": "delivered"})
    ack = OutboxAckRequest(
        lease_id="lease-1", sha256=SHA256_OF_THE_CLIP, relative_path="suspensao/7/1.mp4"
    )

    # Act: send the ACK and read the answer.
    answer = client.outbox_ack(JOB_ID, ack)

    # Assert: it is JobState.DELIVERED itself, not just an equal string.
    assert answer.state is JobState.DELIVERED, "known values must stay enum members"


def test_a_job_record_the_worker_cannot_read_is_an_api_error_not_a_crash():
    # Arrange: GET /jobs/{id} answers with a record this worker cannot read
    # (here, required fields missing, as a much newer or broken server might).
    client = client_answering({"id": JOB_ID, "state": "delivered"})

    # Act + Assert: the lookup raises UnreadableAnswerError, which callers
    # catch as ApiError (the studio then titles the playlist entry without
    # the job and still sends its ACK).
    with pytest.raises(UnreadableAnswerError) as raised:
        client.get_job(JOB_ID)
    assert isinstance(raised.value, ApiError), "callers already handle ApiError"
    assert "job lookup" in str(raised.value), "the message says which answer it could not read"
    assert raised.value.code == "unreadable_answer"


def test_an_answer_that_is_not_json_is_an_api_error_too():
    # Arrange: a proxy error page with status 200 instead of JSON.
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad gateway</html>")

    http = httpx.Client(transport=httpx.MockTransport(answer))
    client = OrchestratorClient(http, ORCHESTRATOR, worker_id="w-1", bearer_token="placeholder")

    # Act + Assert: an ApiError naming the call, not a JSON decode traceback.
    with pytest.raises(ApiError) as raised:
        client.get_job(JOB_ID)
    assert "not JSON" in str(raised.value), "the message says the answer was not JSON"
