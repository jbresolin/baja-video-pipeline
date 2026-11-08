"""Two reviewers on the same video (LIVESTACK-51 item 1): what the review UI shows.

On the 2026-11-07 test João and Alessander opened the same video and both
worked on it. Now:

* opening a job page is the reviewer's first "I am on it" (PUT …/presence);
  the page then repeats it every 20 s (static/js/presence.js);
* when someone else is already on it, a banner says so at the top
  ("Alessander está revisando este vídeo (desde 11:02).") with "Revisar
  mesmo assim", and the form is `inert` (no field, no button works) until
  that is chosen;
* if the other reviewer's pings stop (closed laptop), their presence
  expires and the next answer frees the form by itself: nothing is ever
  locked;
* the queue shows "👤 Alessander" on a row someone else has open.

Setup: `ui` is a signed-in browser ("ana") against a fake orchestrator
(fake_orchestrator.py); `fake_orchestrator.presence` sets who else is on a
job. T0 = 14:00 UTC = 11:00 in São Paulo.
"""

import json
import re
import shutil
import subprocess
from datetime import timedelta

import pytest

from review_ui.catalog import SCRIPT_TEXT
from review_ui.presence_view import names_text, presence_sentence
from review_ui.templating import STATIC_DIR
from shared.models import PresentReviewer
from shared.testing.credentials import REVIEWER_USERNAME
from tests.review_ui.sample_data import T0

JOB = "job-awaiting-1"  # one of the fake's default jobs, awaiting review


def alessander(minutes=2):
    return PresentReviewer(username="Alessander", since=T0 + timedelta(minutes=minutes))


# --- The job page ---


def test_opening_a_job_someone_is_on_shows_the_banner_and_holds_the_form(ui, fake_orchestrator):
    fake_orchestrator.presence[JOB] = [alessander()]

    html = ui.get(f"/review/jobs/{JOB}").text

    # The banner, visible, naming him and since when, with the way out.
    banner = re.search(
        r'<div id="presence-banner" class="banner warning with-action">(.*?)</div>', html, re.S
    )
    assert banner is not None, "the banner is missing or hidden"
    assert "Alessander está revisando este vídeo (desde 11:02)." in banner.group(1)
    assert "Revisar mesmo assim" in banner.group(1)
    # The form waits: inert makes every field and button inside unusable
    # (no script needed), until "Revisar mesmo assim".
    assert '<form id="review-form" class="review-form" autocomplete="off" inert>' in html


def test_opening_a_job_nobody_else_is_on_changes_nothing(ui, fake_orchestrator):
    html = ui.get(f"/review/jobs/{JOB}").text

    assert '<div id="presence-banner" class="banner warning with-action" hidden>' in html
    assert '<form id="review-form" class="review-form" autocomplete="off">' in html


def test_opening_the_page_is_the_first_ping(ui, fake_orchestrator):
    ui.get(f"/review/jobs/{JOB}")

    # So the next person to open it sees this reviewer at once, without
    # waiting for the script's first 20 s ping.
    assert len(fake_orchestrator.requests_to("PUT", f"/jobs/{JOB}/presence")) == 1


def test_if_presence_fails_the_page_still_works(ui, fake_orchestrator):
    # Presence only informs: an error there never blocks reviewing.
    fake_orchestrator.presence[JOB] = [alessander()]
    fake_orchestrator.presence_fails = True

    response = ui.get(f"/review/jobs/{JOB}")

    assert response.status_code == 200
    assert "inert" not in re.search(r'<form id="review-form"[^>]*>', response.text).group(0)


# --- The script's calls ---


def test_the_ping_answers_the_sentence_to_show(ui, fake_orchestrator):
    fake_orchestrator.presence[JOB] = [alessander(), PresentReviewer(username="joao", since=T0)]

    answer = ui.put(f"/review/api/jobs/{JOB}/presence", json={}).json()

    # Two others: both named, since the first one arrived (11:00).
    assert answer["sentence"] == "Alessander e joao estão revisando este vídeo (desde 11:00)."
    assert [reviewer["username"] for reviewer in answer["others"]] == ["Alessander", "joao"]


def test_the_ping_says_nothing_when_nobody_else_is_on_it(ui, fake_orchestrator):
    answer = ui.put(f"/review/api/jobs/{JOB}/presence", json={}).json()

    assert answer == {"others": [], "sentence": None}


def test_leaving_the_page_tells_the_orchestrator(ui, fake_orchestrator):
    response = ui.post(f"/review/api/jobs/{JOB}/presence/leave", json={})

    assert response.status_code == 200
    assert len(fake_orchestrator.requests_to("DELETE", f"/jobs/{JOB}/presence")) == 1


def test_the_ping_needs_a_json_body_like_every_change(ui):
    # The CSRF rule of routes_api.py: a plain cross-site form cannot send JSON.
    assert ui.put(f"/review/api/jobs/{JOB}/presence").status_code == 415


# --- The queue ---


def test_the_queue_marks_rows_someone_else_has_open(ui, fake_orchestrator):
    fake_orchestrator.presence[JOB] = [alessander()]
    # The signed-in reviewer's own open page is not worth a mark.
    fake_orchestrator.presence["job-failed-1"] = [
        PresentReviewer(username=REVIEWER_USERNAME, since=T0)
    ]

    html = ui.get("/review/").text

    assert html.count('class="badge presence"') == 1
    assert ">👤 Alessander</span>" in html


# --- The words ---


def test_names_and_sentences():
    assert names_text(["Alessander"]) == "Alessander"
    assert names_text(["ana", "Alessander", "joao"]) == "ana, Alessander e joao"
    assert presence_sentence([]) is None
    assert presence_sentence([alessander(minutes=32)]) == (
        "Alessander está revisando este vídeo (desde 11:32)."
    )


# --- What the script does with each answer (Node) ---

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const { presenceView } = await import(input.module);
const views = input.cases.map(([blocked, sentence]) => presenceView(blocked, sentence));
console.log(JSON.stringify(views));
"""

SOMEONE = "Alessander está revisando este vídeo (desde 11:02)."
CASES = [
    # Still deciding, he is still there: keep waiting, banner current.
    (True, SOMEONE),
    # Still deciding, his pings stopped (expired) or he left: free the form.
    (True, None),
    # "Revisar mesmo assim" is presenceView(false, null): free, nothing shown.
    (False, None),
    # Working, and someone opens the same video: a quiet note, no blocking.
    (False, SOMEONE),
]


@pytest.fixture(scope="module")
def views() -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; these tests need it")
    payload = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "module": (STATIC_DIR / "js" / "presence.js").as_uri(),
        "cases": CASES,
    }
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"CASES": json.dumps(payload), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_js_keeps_the_form_held_while_the_other_reviewer_is_there(views):
    assert views[0] == {"blocked": True, "banner": SOMEONE, "note": None}


def test_js_frees_the_form_when_the_other_reviewers_pings_stop(views):
    # Nothing is ever locked: a closed laptop expires after 45 s, and the
    # next answer (null) frees the form without anyone clicking.
    assert views[1] == {"blocked": False, "banner": None, "note": None}


def test_js_review_anyway_frees_the_form(views):
    assert views[2] == {"blocked": False, "banner": None, "note": None}


def test_js_a_newcomer_only_gets_a_note_for_whoever_is_working(views):
    assert views[3] == {"blocked": False, "banner": None, "note": SOMEONE}
