"""The studio keeps unsent delivered ACKs in pending_acks.json (design §17).

After a pushed clip is stored, the studio owes the orchestrator a POST
/jobs/{id}/delivered. If the studio restarts before that got through, the
ACK must still be sent. So the list of owed ACKs is written to
`pending_acks.json` in the studio's working folder after every change, and
a new acknowledger (= the studio after a restart) reads it back.

"Restart" in these tests = throw the DeliveryAcknowledger away and build a
new one on the same file, exactly what the daemon does at start.
"""

import json
import threading

import pytest

from shared.layout import LATEST_PLAYLIST_NAME
from tests.worker.conftest import make_api_client
from tests.worker.fake_orchestrator import BASE_URL, FakeOrchestrator
from tests.worker.fakes import RoutingTransport
from worker.studio.acknowledger import DeliveryAcknowledger
from worker.studio.library import StudioLibrary
from worker.studio.pending_acks import PENDING_ACKS_FILE_NAME, PendingDelivery


@pytest.fixture
def studio_parts(tmp_path):
    """The fake orchestrator, the studio's clip folder, and where the file lives."""
    orchestrator = FakeOrchestrator(tmp_path / "vps")
    network = RoutingTransport()
    network.add_app(BASE_URL, orchestrator.app)
    library = StudioLibrary(tmp_path / "box")
    pending_file = tmp_path / "studio-work" / PENDING_ACKS_FILE_NAME
    return orchestrator, network, library, pending_file


def start_acknowledger(studio_parts) -> DeliveryAcknowledger:
    """What the daemon builds at every start (the file is read here)."""
    orchestrator, network, library, pending_file = studio_parts
    return DeliveryAcknowledger(
        make_api_client(network),
        library,
        threading.Event(),
        retry_delay_seconds=0,
        pending_acks_path=pending_file,
    )


def clip(job_id: str, relative_path: str = "enduro/1/1.mp4") -> PendingDelivery:
    return PendingDelivery(job_id, relative_path, "a" * 64, 1234)


def saved_job_ids(pending_file) -> list[str]:
    return [item["job_id"] for item in json.loads(pending_file.read_text())]


def test_an_ack_that_failed_before_a_restart_is_sent_after_it(studio_parts):
    orchestrator, _, _, pending_file = studio_parts
    # Before the restart: a clip arrives, but the orchestrator is down
    # (503), so the ACK cannot be sent.
    orchestrator.delivered_calls_to_fail = 1
    before_restart = start_acknowledger(studio_parts)
    before_restart.submit(clip("job-1"))
    before_restart.handle_all_pending()
    assert orchestrator.delivered == []
    # The owed ACK is on disk, not only in memory.
    assert saved_job_ids(pending_file) == ["job-1"]

    # The studio restarts (new acknowledger, same file) and the
    # orchestrator is back. Its first round sends the old ACK...
    after_restart = start_acknowledger(studio_parts)
    assert after_restart.pending_count() == 1
    after_restart.handle_all_pending()

    [(acked_job_id, delivered)] = orchestrator.delivered
    assert acked_job_id == "job-1" and delivered.size_bytes == 1234
    # ...and, once accepted, removes it from the file.
    assert saved_job_ids(pending_file) == []


def test_an_accepted_ack_is_removed_from_the_file(studio_parts):
    orchestrator, _, _, pending_file = studio_parts
    acknowledger = start_acknowledger(studio_parts)

    acknowledger.submit(clip("job-1"))
    # Right after the clip arrived, the ACK is owed and saved.
    assert saved_job_ids(pending_file) == ["job-1"]
    acknowledger.handle_all_pending()

    # Accepted: gone from memory and from the file.
    assert len(orchestrator.delivered) == 1
    assert acknowledger.pending_count() == 0
    assert saved_job_ids(pending_file) == []
    # The file was replaced through a temporary file; none is left behind.
    assert sorted(p.name for p in pending_file.parent.iterdir()) == [PENDING_ACKS_FILE_NAME]


@pytest.mark.parametrize(("status", "code"), [(409, "illegal_transition"), (404, "not_found")])
def test_an_ack_the_orchestrator_says_no_longer_applies_is_dropped(studio_parts, status, code):
    # E.g. the reviewer reprocessed the job meanwhile (409) or the job is
    # gone (404). Sending it again would get the same answer forever.
    orchestrator, _, _, pending_file = studio_parts
    orchestrator.delivered_refusal = (status, code)
    acknowledger = start_acknowledger(studio_parts)

    acknowledger.submit(clip("job-1"))
    acknowledger.handle_all_pending()

    assert acknowledger.pending_count() == 0
    assert saved_job_ids(pending_file) == []


def test_an_ack_refused_for_lack_of_auth_is_kept_for_later(studio_parts):
    # 401 says "fix the setup", not "this ACK is wrong": keep it, so it is
    # sent once the token problem is sorted out (and survives a restart).
    orchestrator, _, _, pending_file = studio_parts
    orchestrator.delivered_refusal = (401, "unauthenticated")
    acknowledger = start_acknowledger(studio_parts)

    acknowledger.submit(clip("job-1"))
    acknowledger.handle_all_pending()

    assert acknowledger.pending_count() == 1
    assert saved_job_ids(pending_file) == ["job-1"]


def test_a_replayed_ack_does_not_move_its_clip_back_to_the_top_of_the_playlist(
    studio_parts,
):
    # Setup: clip 1 arrives and goes into latest.m3u, but its ACK fails.
    # Then clip 2 arrives; in that round clip 1's retry fails once more
    # (the oldest goes first) while clip 2's ACK gets through. Clip 2 is
    # now at the top of the playlist and clip 1's ACK is still owed.
    orchestrator, _, library, _ = studio_parts
    acknowledger = start_acknowledger(studio_parts)
    orchestrator.delivered_calls_to_fail = 1
    acknowledger.submit(clip("job-1", "enduro/1/1.mp4"))
    acknowledger.handle_all_pending()
    orchestrator.delivered_calls_to_fail = 1
    acknowledger.submit(clip("job-2", "enduro/2/1.mp4"))
    acknowledger.handle_all_pending()
    assert [job_id for job_id, _ in orchestrator.delivered] == ["job-2"]

    # After a restart, the old ACK for clip 1 is replayed...
    start_acknowledger(studio_parts).handle_all_pending()

    # ...and sent, but the file remembered that clip 1 was already in the
    # playlist, so clip 2 (the newest) stays at the top.
    assert [job_id for job_id, _ in orchestrator.delivered] == ["job-2", "job-1"]
    paths = [
        line
        for line in (library.root / LATEST_PLAYLIST_NAME).read_text().splitlines()
        if not line.startswith("#")
    ]
    assert paths == ["enduro/2/1.mp4", "enduro/1/1.mp4"]


def test_an_unreadable_file_is_set_aside_and_the_studio_starts_empty(studio_parts):
    # A damaged file (e.g. edited by hand) must not stop the studio.
    _, _, _, pending_file = studio_parts
    pending_file.parent.mkdir(parents=True)
    pending_file.write_text("this is not json")

    acknowledger = start_acknowledger(studio_parts)

    assert acknowledger.pending_count() == 0
    # Kept for a human to look at, under another name.
    aside = pending_file.with_name(PENDING_ACKS_FILE_NAME + ".unreadable")
    assert aside.read_text() == "this is not json"
