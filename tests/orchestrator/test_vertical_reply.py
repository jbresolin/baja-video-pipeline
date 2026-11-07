"""The bot asks for horizontal videos, once per video (LIVESTACK-50).

When the first holder's probe report shows a video filmed upright, the
bot quotes that video with the text from Settings (default "📱 Vídeo na
vertical. Da próxima vez, grave na horizontal, por favor."). Only the
job's *first* report can trigger it, so a second probe (a re-rendered
proxy) and a restart of the orchestrator never send it again. An empty
text turns it off.

The fake Evolution records every text the orchestrator sends
(`harness.evolution.sent`), so these tests read the WhatsApp side too.
"""

from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import claim_ok, probe_and_make_proxy, receive_job
from shared.models import TaskKind, WorkerRole
from shared.protocol import LEASE_ID_HEADER
from shared.vertical_video_message import DEFAULT_VERTICAL_VIDEO_MESSAGE
from tests.orchestrator.test_render_options import LANDSCAPE_REPORT, PORTRAIT_PHONE_REPORT


def vertical_replies(harness) -> list:
    """The texts the bot sent that are the vertical-video reply."""
    message = harness.context.admin_settings.current.vertical_video_message
    return [sent for sent in harness.evolution.sent if sent.text == message]


def first_probe(harness, report):
    """A video arrives; the master holds it and sends `report` as its first probe."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.1")
    job_id = receive_job(harness)
    probe_and_make_proxy(harness, master, job_id, report)
    return master, job_id


def probe_again(harness, master, job_id, report):
    """A reviewer asks for the proxy again; the holder probes and reports once more."""
    asked = harness.client.post(f"/jobs/{job_id}/regenerate-proxy", auth=harness.reviewer_auth)
    assert asked.status_code == 202, asked.text
    harness.heartbeat(master, held_job_ids=[job_id])
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    response = harness.client.put(
        f"/jobs/{job_id}/media-info",
        json=report,
        headers={**master.headers, LEASE_ID_HEADER: lease_id},
    )
    assert response.status_code == 204, response.text


def test_a_vertical_video_gets_one_quoted_reply(harness):
    # What it does: the first probe report of an upright phone video.
    _, job_id = first_probe(harness, PORTRAIT_PHONE_REPORT)

    # One text, the default from Settings, quoting the very video it is
    # about (its WhatsApp message id), sent to the sender's number.
    replies = vertical_replies(harness)
    assert [reply.text for reply in replies] == [DEFAULT_VERTICAL_VIDEO_MESSAGE]
    job = harness.job(job_id)
    assert replies[0].quoted["key"]["id"] == job["source"]["message_id"]
    assert replies[0].number in job["source"]["sender_jid"]
    # Quoted like the other replies: the document's type and file name.
    assert "documentMessage" in replies[0].quoted["message"]
    # Fake Evolution checks every request against Evolution 2.3.7's schemas.
    assert harness.evolution.problems == []
    # The event log says it was sent.
    events = harness.client.get(f"/jobs/{job_id}/events", auth=harness.reviewer_auth).json()
    assert "vertical_reply_sent" in [event["kind"] for event in events["events"]]


def test_the_reaction_on_the_video_stays_as_it_was(harness):
    # The video is used (with blurred sides), so the ✅ from intake stays:
    # the bot only adds the text, it does not react again.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.1")
    job_id = receive_job(harness)
    reactions_before_probe = list(harness.evolution.reactions)
    probe_and_make_proxy(harness, master, job_id, PORTRAIT_PHONE_REPORT)
    assert harness.evolution.reactions == reactions_before_probe


def test_a_second_probe_of_the_same_video_sends_nothing(harness):
    master, job_id = first_probe(harness, PORTRAIT_PHONE_REPORT)

    probe_again(harness, master, job_id, PORTRAIT_PHONE_REPORT)

    assert len(vertical_replies(harness)) == 1


def test_a_restarted_orchestrator_does_not_send_it_again(tmp_path):
    # Setup: the reply went out before the orchestrator restarted.
    first = build_harness(tmp_path / "data")
    master, job_id = first_probe(first, PORTRAIT_PHONE_REPORT)
    assert len(vertical_replies(first)) == 1
    first.client.close()
    first.context.database.close()

    # What it does: a new orchestrator on the same data; the holder probes again.
    restarted = build_harness(tmp_path / "data")
    probe_again(restarted, master, job_id, PORTRAIT_PHONE_REPORT)

    # The stored report says this video was already probed: no second text.
    assert vertical_replies(restarted) == []
    restarted.client.close()
    restarted.context.database.close()


def test_a_horizontal_video_gets_no_reply(harness):
    first_probe(harness, LANDSCAPE_REPORT)
    assert vertical_replies(harness) == []


def test_the_text_comes_from_settings_and_an_empty_one_turns_it_off(harness):
    changed = harness.client.patch(
        "/admin/settings",
        json={"vertical_video_message": "  Grave deitado!\r\n "},
        auth=harness.admin_auth,
    )
    assert changed.status_code == 200, changed.text
    # Stored trimmed, with \n line ends, like the welcome message.
    assert changed.json()["settings"]["vertical_video_message"] == "Grave deitado!"
    first_probe(harness, PORTRAIT_PHONE_REPORT)
    assert [sent.text for sent in vertical_replies(harness)] == ["Grave deitado!"]

    harness.client.patch(
        "/admin/settings", json={"vertical_video_message": ""}, auth=harness.admin_auth
    )
    texts_before = len(harness.evolution.sent)
    master = harness.enroll(WorkerRole.MASTER, "master-2", tailnet_address="100.70.0.2")
    second_job = receive_job(harness, data=b"another upright video" * 100)
    probe_and_make_proxy(harness, master, second_job, PORTRAIT_PHONE_REPORT)
    # Only intake's own messages (none for a good document) were added.
    assert len(harness.evolution.sent) == texts_before


def test_a_refused_reply_is_recorded_and_not_retried(harness):
    harness.evolution.refuse_texts_with = 500
    master, job_id = first_probe(harness, PORTRAIT_PHONE_REPORT)

    events = harness.client.get(f"/jobs/{job_id}/events", auth=harness.reviewer_auth).json()
    assert "vertical_reply_failed" in [event["kind"] for event in events["events"]]
    # The proxy and the review are not affected by the failed text.
    assert harness.job(job_id)["state"] == "awaiting_review"

    harness.evolution.refuse_texts_with = None
    probe_again(harness, master, job_id, PORTRAIT_PHONE_REPORT)
    assert vertical_replies(harness) == []
