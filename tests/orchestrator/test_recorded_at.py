"""When was each video filmed? Job.recorded_at (LIVESTACK-45).

The review queue and the job page list a car's videos in filming order, so
the reviewer can number the passagens. Arrival order is not filming order
(a phone sends three passes at once; two filmmakers send in any order), so
every job gets a recording time from the best source there is:

1. the raw's own creation_time (from the probe report the first holder
   sends), when plausible: not after the bot received the video and not
   more than 7 days before → source "metadata" (exact);
2. else the WhatsApp send time → source "whatsapp" (a hint);
3. else the moment the bot received it → source "received".

All times are on the event day, 2026-03-26, in UTC (São Paulo is UTC-3,
so 17:02 UTC is 14:02 on the track).
"""

from datetime import UTC, datetime

import pytest

from orchestrator.domain.recording_time import (
    choose_recorded_at,
    creation_time_from_probe,
    parse_creation_time,
)
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import claim_ok, hold_raw, receive_job
from shared.models import ProbeReport, RecordedAtSource, TaskKind, WorkerRole
from shared.protocol import LEASE_ID_HEADER

# The bot receives the video at 14:40 on the track (17:40 UTC).
RECEIVED = datetime(2026, 3, 26, 17, 40, tzinfo=UTC)
# The filmmaker pressed "send" two minutes earlier.
SENT = datetime(2026, 3, 26, 17, 38, tzinfo=UTC)
# The camera started recording at 14:02.
FILMED = datetime(2026, 3, 26, 17, 2, 11, tzinfo=UTC)


def probe_with_creation_time(value: str | None, *, on_stream: bool = False) -> dict:
    """An ffprobe report (as the worker sends it) with creation_time where asked."""
    tags = {"creation_time": value} if value is not None else {}
    return {
        "format": {"format_name": "mov,mp4", "tags": {} if on_stream else tags},
        "streams": [
            {"codec_type": "audio", "codec_name": "aac", "tags": {}},
            {"codec_type": "video", "codec_name": "h264", "tags": tags if on_stream else {}},
        ],
        "location_present": False,
    }


# --- The rules on their own (no orchestrator needed) ---


def test_plausible_metadata_wins():
    # The file says it was filmed at 14:02, 38 minutes before the bot got
    # it: believable, so it is the recording time, marked exact.
    probe = ProbeReport.model_validate(probe_with_creation_time("2026-03-26T17:02:11.000000Z"))
    assert choose_recorded_at(received_at=RECEIVED, sent_at=SENT, probe=probe) == (
        FILMED,
        RecordedAtSource.METADATA,
    )


def test_metadata_after_receipt_is_rejected():
    # A phone whose clock runs an hour fast says 15:02 + 1 h, which is
    # after the bot received the video: impossible, so the send time is
    # used instead.
    probe = ProbeReport.model_validate(probe_with_creation_time("2026-03-26T18:02:11Z"))
    assert choose_recorded_at(received_at=RECEIVED, sent_at=SENT, probe=probe) == (
        SENT,
        RecordedAtSource.WHATSAPP,
    )


def test_metadata_thirty_days_old_is_rejected():
    # A camera that says 2026-02-24 (30 days before) has a wrong clock, or
    # this is not today's video: not believed, more than 7 days back.
    probe = ProbeReport.model_validate(probe_with_creation_time("2026-02-24T17:02:11Z"))
    assert choose_recorded_at(received_at=RECEIVED, sent_at=SENT, probe=probe) == (
        SENT,
        RecordedAtSource.WHATSAPP,
    )


def test_no_metadata_uses_the_whatsapp_time():
    # WhatsApp often strips creation_time from a video sent as *video*.
    probe = ProbeReport.model_validate(probe_with_creation_time(None))
    assert choose_recorded_at(received_at=RECEIVED, sent_at=SENT, probe=probe) == (
        SENT,
        RecordedAtSource.WHATSAPP,
    )


def test_neither_uses_the_time_received():
    # No creation_time and no WhatsApp timestamp: the bot's own clock.
    assert choose_recorded_at(received_at=RECEIVED, sent_at=None, probe=None) == (
        RECEIVED,
        RecordedAtSource.RECEIVED,
    )


def test_a_whatsapp_time_after_receipt_is_not_believed_either():
    # The same plausibility test protects against a broken timestamp.
    later = datetime(2026, 3, 26, 18, 0, tzinfo=UTC)
    assert choose_recorded_at(received_at=RECEIVED, sent_at=later) == (
        RECEIVED,
        RecordedAtSource.RECEIVED,
    )


def test_the_first_video_streams_creation_time_is_used_when_the_container_has_none():
    report = ProbeReport.model_validate(
        probe_with_creation_time("2026-03-26T17:02:11Z", on_stream=True)
    )
    assert creation_time_from_probe(report) == FILMED


@pytest.mark.parametrize(
    "value, expected",
    [
        # The usual ffprobe form, with microseconds and Z.
        ("2026-03-26T17:02:11.000000Z", FILMED),
        # Old muxers: a space and no zone, which MP4 means as UTC.
        ("2026-03-26 17:02:11", FILMED),
        # With an offset (some cameras): converted to UTC.
        ("2026-03-26T14:02:11-03:00", FILMED),
        # Not a time at all.
        ("yesterday", None),
        ("", None),
        (None, None),
        (1774544531, None),
    ],
)
def test_creation_time_parsing(value, expected):
    assert parse_creation_time(value) == expected


# --- Through the orchestrator: intake, then the probe report ---


@pytest.fixture
def harness_on_event_day(tmp_path):
    """An orchestrator whose clock says 2026-03-26 17:40 UTC (14:40 on the track)."""
    built = build_harness(tmp_path / "data", clock_start=RECEIVED)
    yield built
    built.client.close()
    built.context.database.close()


def with_whatsapp_time(moment: datetime | None):
    """A payload changer for send_video: sets (or removes) messageTimestamp."""

    def reshape(payload: dict) -> dict:
        if moment is None:
            payload["data"].pop("messageTimestamp", None)
        else:
            payload["data"]["messageTimestamp"] = int(moment.timestamp())
        return payload

    return reshape


def send_probe_report(harness, job_id: str, report: dict) -> None:
    """Be the first holder: hold the raw, claim make_proxy, PUT the probe report."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    hold_raw(harness, master, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    response = harness.client.put(
        f"/jobs/{job_id}/media-info",
        json=report,
        headers={**master.headers, LEASE_ID_HEADER: lease_id},
    )
    assert response.status_code == 204, response.text


def recorded(job: dict) -> tuple[str, str]:
    return job["recorded_at"], job["recorded_at_source"]


def test_a_new_job_starts_with_the_whatsapp_time(harness_on_event_day):
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(SENT))

    job = harness.job(job_id)
    # Until a holder has probed the raw, the send time is the best guess.
    assert recorded(job) == ("2026-03-26T17:38:00Z", "whatsapp")
    # Both intake times are kept on the job's source, for support.
    assert job["source"]["sent_at"] == "2026-03-26T17:38:00Z"
    assert job["source"]["received_at"] == "2026-03-26T17:40:00Z"


def test_a_document_with_creation_time_gets_the_exact_time_once_probed(harness_on_event_day):
    # Sent as a document, WhatsApp leaves the file as it is, creation_time included.
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(SENT))
    version_before = harness.job(job_id)["version"]

    send_probe_report(harness, job_id, probe_with_creation_time("2026-03-26T17:02:11.000000Z"))

    job = harness.job(job_id)
    assert recorded(job) == ("2026-03-26T17:02:11Z", "metadata")
    # Machine activity: the review version does not move, so a reviewer
    # with the page open can still save.
    assert job["version"] == version_before


def test_a_video_message_without_creation_time_keeps_the_whatsapp_time(harness_on_event_day):
    # Sent as a *video*, WhatsApp recompresses it and often drops
    # creation_time: the probe report has none, so the send time stays.
    harness = harness_on_event_day
    job_id = receive_job(harness, as_video_message=True, reshape=with_whatsapp_time(SENT))

    send_probe_report(harness, job_id, probe_with_creation_time(None))

    job = harness.job(job_id)
    assert job["low_res"] is True
    assert recorded(job) == ("2026-03-26T17:38:00Z", "whatsapp")


def test_metadata_after_receipt_is_not_stored(harness_on_event_day):
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(SENT))

    send_probe_report(harness, job_id, probe_with_creation_time("2026-03-26T18:02:11Z"))

    assert recorded(harness.job(job_id)) == ("2026-03-26T17:38:00Z", "whatsapp")


def test_metadata_thirty_days_old_is_not_stored(harness_on_event_day):
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(SENT))

    send_probe_report(harness, job_id, probe_with_creation_time("2026-02-24T17:02:11Z"))

    assert recorded(harness.job(job_id)) == ("2026-03-26T17:38:00Z", "whatsapp")


def test_no_timestamp_and_no_metadata_means_the_time_received(harness_on_event_day):
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(None))

    send_probe_report(harness, job_id, probe_with_creation_time(None))

    job = harness.job(job_id)
    assert job["source"]["sent_at"] is None
    assert recorded(job) == ("2026-03-26T17:40:00Z", "received")


def test_the_queue_summary_carries_the_recording_time(harness_on_event_day):
    harness = harness_on_event_day
    job_id = receive_job(harness, reshape=with_whatsapp_time(SENT))

    queue = harness.client.get(
        "/review/queue", params={"state": "received"}, auth=harness.reviewer_auth
    ).json()

    [item] = [item for item in queue["items"] if item["id"] == job_id]
    assert (item["recorded_at"], item["recorded_at_source"]) == (
        "2026-03-26T17:38:00Z",
        "whatsapp",
    )
