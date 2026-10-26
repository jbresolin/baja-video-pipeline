"""Tests for shared.models: the pydantic models used by every API body.

pydantic checks data when a model is created: wrong types or values raise
ValidationError. These tests check the rules we rely on (hash format,
cut order, unknown fields rejected, task kinds told apart) and that
models survive a round trip to JSON and back unchanged.
"""

from datetime import timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from shared.models import (
    DownloadRawTask,
    ErrorResponse,
    HeartbeatRequest,
    IntakeSource,
    Job,
    JobState,
    JobSummary,
    Lease,
    NextTaskResponse,
    ProcessTask,
    RawSource,
    ReceivedAs,
    ReviewAction,
    ReviewUpdate,
    Task,
    TaskKind,
    utc_now,
)

GOOD_SHA = "a" * 64


def make_job(**overrides) -> Job:
    """A valid Job with sensible defaults; tests change only what they need."""
    fields = {
        "id": "job-1",
        "state": JobState.RECEIVED,
        "source": IntakeSource(message_id="msg-1", received_as=ReceivedAs.DOCUMENT),
        "sha256": GOOD_SHA,
        "size_bytes": 500_000_000,
    }
    fields.update(overrides)
    return Job(**fields)


def test_job_round_trips_through_json():
    # Convert to JSON text and back. If any field were lost or changed on
    # the way, the two objects would not be equal.
    job = make_job(prova="Enduro", car_number=42, passagem=3, cut_in_s=1.0, cut_out_s=9.5)
    assert Job.model_validate_json(job.model_dump_json()) == job


def test_job_state_is_written_as_plain_text_in_json():
    # Other services (and humans reading the DB) see "received", not an
    # internal Python name.
    assert make_job().model_dump(mode="json")["state"] == "received"


def test_new_job_defaults():
    job = make_job()
    assert job.version == 1
    assert job.low_res is False
    assert job.raw_on_vps is True  # the VPS has the raw until 2 holders confirm
    assert job.holders == [] and job.leases == []
    assert job.show_pass is True
    # Timestamps carry a timezone (UTC), never "naive" local times.
    assert job.created_at.tzinfo is not None


@pytest.mark.parametrize("bad_sha", ["A" * 64, "a" * 63, "g" * 64, ""])
def test_sha256_must_be_64_lowercase_hex(bad_sha):
    # Uppercase, too short, non-hex and empty are all refused, so two
    # services can compare hashes as plain strings.
    with pytest.raises(ValidationError):
        make_job(sha256=bad_sha)


def test_cut_out_must_be_after_cut_in():
    with pytest.raises(ValidationError):
        make_job(cut_in_s=10.0, cut_out_s=5.0)


def test_negative_cut_is_refused():
    with pytest.raises(ValidationError):
        make_job(cut_in_s=-1.0)


def test_passagem_starts_at_one():
    with pytest.raises(ValidationError):
        make_job(passagem=0)


def test_unknown_fields_are_rejected():
    # A typo like "cut_in" instead of "cut_in_s" must fail loudly instead
    # of being silently ignored (and the cut silently not saved).
    with pytest.raises(ValidationError):
        ReviewUpdate(expected_version=1, cut_in=3.0)


def test_review_update_defaults_to_save_and_checks_cut_order():
    assert ReviewUpdate(expected_version=1).action == ReviewAction.SAVE
    with pytest.raises(ValidationError):
        ReviewUpdate(expected_version=1, cut_in_s=5.0, cut_out_s=5.0)


def test_job_summary_is_built_from_a_job():
    job = make_job(source=IntakeSource(message_id="m", sender_name="Ana", received_as="video"))
    summary = JobSummary.from_job(job)
    assert summary.id == job.id
    assert summary.sender_name == "Ana"


def test_task_union_picks_the_model_from_the_kind_field():
    # A /next response arrives as JSON. The "kind" field alone decides which
    # task model it becomes; that is what Field(discriminator="kind") does.
    data = {
        "kind": "download_raw",
        "job_id": "job-1",
        "sha256": GOOD_SHA,
        "size_bytes": 10,
        "sources": [{"kind": "vps", "url": "http://vps/jobs/job-1/raw"}],
    }
    task = TypeAdapter(Task).validate_python(data)
    assert isinstance(task, DownloadRawTask)
    assert task.sources[0].url.endswith("/raw")


def test_next_task_response_can_be_empty_or_hold_a_task():
    empty = NextTaskResponse.model_validate_json('{"task": null, "retry_after_s": 5}')
    assert empty.task is None

    process = ProcessTask(
        job_id="job-1",
        raw_sha256=GOOD_SHA,
        has_audio=True,
        low_res=False,
        cut_in_s=1.0,
        cut_out_s=2.0,
        prova="Enduro",
        car_number=42,
        team_name=None,
        passagem=1,
        show_pass=True,
        clip_relative_path="enduro/42/1.mp4",
        outbox_upload_url="http://vps/jobs/job-1/clip",
    )
    round_tripped = NextTaskResponse.model_validate_json(
        NextTaskResponse(task=process).model_dump_json()
    )
    # After the trip through JSON it is still a ProcessTask, not a dict.
    assert isinstance(round_tripped.task, ProcessTask)
    assert round_tripped.task.kind == TaskKind.PROCESS


def test_download_task_needs_at_least_one_source():
    with pytest.raises(ValidationError):
        DownloadRawTask(job_id="j", sha256=GOOD_SHA, size_bytes=1, sources=[])
    assert RawSource(kind="peer", url="http://peer:8770/raw/j", worker_id="w2").worker_id == "w2"


def test_heartbeat_defaults_to_empty_lists():
    heartbeat = HeartbeatRequest(free_bytes=1, storage_cap_bytes=2)
    assert heartbeat.current_lease_ids == []
    assert heartbeat.measurements == []


def test_lease_holds_its_expiry():
    now = utc_now()
    lease = Lease(
        lease_id="l1",
        job_id="j",
        worker_id="w",
        task_kind=TaskKind.PROCESS,
        acquired_at=now,
        expires_at=now + timedelta(seconds=45),
    )
    assert lease.expires_at > lease.acquired_at


def test_error_response_shape():
    # Every error in the API looks like {"error": {"code", "message", "details"}}.
    body = {"error": {"code": "lease_lost", "message": "lease expired"}}
    parsed = ErrorResponse.model_validate(body)
    assert parsed.error.code == "lease_lost"
    assert parsed.error.details == {}
