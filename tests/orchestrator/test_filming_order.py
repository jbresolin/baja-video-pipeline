"""The review queue and a car's videos, in filming order (LIVESTACK-45).

A reviewer must say whether a video is a car's 1st, 2nd or nth passagem
in a prova. Two things help, and both are tested here at the API:

* GET /review/queue lists videos oldest *recording* first, not in the
  order they arrived (a phone may send three passes at once).
* GET /review/car-videos?prova=…&car_number=… lists every other video of
  that car in that prova (discarded ones left out), also in filming order,
  for the job page's "Outros vídeos de <prova>, carro <n>" panel.

Test data looks like the real thing: prova "Suspensão", car 7 of
"Baja UFPR", on 2026-03-26 (times in UTC; São Paulo is UTC-3).
"""

from datetime import UTC, datetime

import pytest

from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import (
    ensure_prova,
    probe_and_make_proxy,
    receive_job,
    save_review_fields,
)
from shared.models import WorkerRole

# The last of the three videos arrives at 14:45 on the track.
EVENT_DAY_AFTERNOON = datetime(2026, 3, 26, 17, 45, tzinfo=UTC)


def probe_filmed_at(creation_time: str) -> dict:
    """A probe report whose file says it was filmed at `creation_time` (UTC)."""
    return {
        "format": {"format_name": "mov,mp4", "tags": {"creation_time": creation_time}},
        "streams": [{"codec_type": "video", "codec_name": "h264"}],
        "location_present": False,
    }


@pytest.fixture
def harness(tmp_path):
    """An orchestrator on the event day, with Baja UFPR's car 7 and the prova Suspensão."""
    built = build_harness(tmp_path / "data", clock_start=EVENT_DAY_AFTERNOON)
    built.add_team(7, "Baja UFPR")
    ensure_prova(built, "Suspensão")
    yield built
    built.client.close()
    built.context.database.close()


@pytest.fixture
def master(harness):
    return harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")


def video_filmed_at(harness, master, creation_time: str) -> str:
    """A video that arrives now and whose file says when it was filmed; returns its job id.

    Each call moves the clock one minute, so videos arrive one after the other.
    """
    harness.clock.advance(60)
    job_id = receive_job(harness)
    probe_and_make_proxy(harness, master, job_id, probe_filmed_at(creation_time))
    return job_id


def queue_ids(harness) -> list[str]:
    queue = harness.client.get("/review/queue", auth=harness.reviewer_auth)
    assert queue.status_code == 200, queue.text
    return [item["id"] for item in queue.json()["items"]]


def car_videos(harness, prova: str, car_number: int = 7) -> dict:
    response = harness.client.get(
        "/review/car-videos",
        params={"prova": prova, "car_number": car_number},
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- The queue ---


def test_the_queue_is_in_filming_order_not_arrival_order(harness, master):
    # Three passes arrive in the reverse of the order they were filmed: the
    # 14:31 video first, then 14:15, then 14:02 (a filmmaker sending the
    # newest first).
    filmed_third = video_filmed_at(harness, master, "2026-03-26T17:31:00Z")
    filmed_second = video_filmed_at(harness, master, "2026-03-26T17:15:00Z")
    filmed_first = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")

    # The queue shows them oldest recording first, the order to number them in.
    assert queue_ids(harness) == [filmed_first, filmed_second, filmed_third]


def test_equal_recording_times_keep_arrival_order(harness, master):
    # Two files that say the same second (two cameras on one pass): the
    # one that arrived first comes first, so the order never jumps.
    arrived_first = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    arrived_second = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    assert queue_ids(harness) == [arrived_first, arrived_second]


# --- One car's videos in one prova ---


def test_a_car_with_one_video_lists_just_that_video(harness, master):
    job_id = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    save_review_fields(harness, job_id, prova="Suspensão", car_number=7)

    answer = car_videos(harness, "Suspensão")

    # The review UI removes the asking job itself; nothing else is there.
    assert [item["id"] for item in answer["items"]] == [job_id]
    assert answer["prova_slug"] == "suspensao"
    assert answer["car_number"] == 7


def test_the_cars_videos_come_in_filming_order_with_their_passagem(harness, master):
    second = video_filmed_at(harness, master, "2026-03-26T17:31:00Z")
    first = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    save_review_fields(harness, second, prova="Suspensão", car_number=7, passagem=2)
    save_review_fields(harness, first, prova="Suspensão", car_number=7, passagem=1)

    items = car_videos(harness, "Suspensão")["items"]

    assert [(item["id"], item["passagem"]) for item in items] == [(first, 1), (second, 2)]
    # Each carries what the panel shows: recording time and how sure it is,
    # the state and the thumbnail.
    assert items[0]["recorded_at"] == "2026-03-26T17:02:00Z"
    assert items[0]["recorded_at_source"] == "metadata"
    assert items[0]["state"] == "awaiting_review"
    assert items[0]["thumbnail_url"]


def test_other_cars_and_other_provas_are_left_out(harness, master):
    ensure_prova(harness, "Aceleração")
    ours = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    other_car = video_filmed_at(harness, master, "2026-03-26T17:05:00Z")
    other_prova = video_filmed_at(harness, master, "2026-03-26T17:08:00Z")
    save_review_fields(harness, ours, prova="Suspensão", car_number=7)
    save_review_fields(harness, other_car, prova="Suspensão", car_number=8)
    save_review_fields(harness, other_prova, prova="Aceleração", car_number=7)

    assert [item["id"] for item in car_videos(harness, "Suspensão")["items"]] == [ours]


def test_a_discarded_video_is_left_out(harness, master):
    kept = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    junk = video_filmed_at(harness, master, "2026-03-26T17:10:00Z")
    save_review_fields(harness, kept, prova="Suspensão", car_number=7, passagem=1)
    job = save_review_fields(harness, junk, prova="Suspensão", car_number=7, passagem=2)
    # The reviewer throws the second one away (an accidental video).
    discarded = harness.client.patch(
        f"/jobs/{junk}/review",
        json={"expected_version": job["version"], "action": "discard"},
        auth=harness.reviewer_auth,
    )
    assert discarded.status_code == 200, discarded.text

    # Its passagem 2 must not count as "already used".
    assert [item["id"] for item in car_videos(harness, "Suspensão")["items"]] == [kept]


def test_a_renamed_prova_still_finds_every_video(harness, master):
    job_id = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    save_review_fields(harness, job_id, prova="Suspensão", car_number=7, passagem=1)
    # The admin renames the prova. The job is relabelled, the slug (the
    # clip folder, "suspensao") stays.
    prova = ensure_prova(harness, "Suspensão")
    renamed = harness.client.patch(
        f"/admin/provas/{prova['id']}",
        json={"name": "Suspensão e Tração"},
        headers=harness.admin_headers,
    )
    assert renamed.status_code == 200, renamed.text

    # Found by the new name, by the slug, and by the old name a page
    # loaded before the rename still shows: all three are one folder.
    for asked_as in ("Suspensão e Tração", "suspensao", "Suspensão"):
        answer = car_videos(harness, asked_as)
        assert [item["id"] for item in answer["items"]] == [job_id], asked_as
        assert answer["prova_slug"] == "suspensao"


def test_a_prova_written_differently_finds_the_same_folder(harness, master):
    # "suspensão" (lower case) is not a name in the table, but it is the
    # same folder; the job page sends whatever the select says.
    job_id = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    save_review_fields(harness, job_id, prova="Suspensão", car_number=7)
    assert [item["id"] for item in car_videos(harness, "suspensão")["items"]] == [job_id]


def test_a_prova_without_letters_or_digits_finds_nothing(harness):
    answer = car_videos(harness, "---")
    assert answer == {"prova_slug": None, "car_number": 7, "items": []}


def test_car_videos_needs_a_signed_in_reviewer(harness):
    response = harness.client.get(
        "/review/car-videos", params={"prova": "Suspensão", "car_number": 7}
    )
    assert response.status_code == 401


def test_car_videos_needs_both_parameters(harness):
    response = harness.client.get(
        "/review/car-videos", params={"prova": "Suspensão"}, auth=harness.reviewer_auth
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
