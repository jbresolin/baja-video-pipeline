"""The review UI's side of LIVESTACK-50: vertical videos, Girar, Enquadramento, Sem áudio.

* The queue marks vertical videos; the job page says the video's shape and
  has the "Imagem e som" box (Girar, Enquadramento with the crop band's
  place, Sem áudio). "Enquadramento" is hidden for a 16:9 picture, where
  it changes nothing.
* A clip whose options changed after delivery says so in words.
* The orchestrator's new waiting sentence is shown in pt-BR.
* Frota says what each worker version can render.
* Configurações holds the bot's reply to vertical videos.
* static/js/render_options.js follows the same rules as shared/framing.py
  (checked in Node on the same cases).

Setup (conftest.py): `ui` is a signed-in browser against a fake
orchestrator holding a few jobs; `admin` is an admin's browser against
the real orchestrator (`harness`).
"""

import json
import re
import shutil
import subprocess

import pytest

from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER
from review_ui.catalog import SCRIPT_TEXT
from review_ui.clip_inputs_view import difference_sentence
from review_ui.clips_and_fleet_view import render_features_text
from review_ui.job_view import NO_HOLDER_CAN_RENDER_REASON, describe_waiting_reason
from review_ui.render_options_view import framing_applies, orientation_text
from review_ui.templating import STATIC_DIR
from shared.framing import fills_frame_like_before, plan_frame
from shared.models import Framing, RenderInputField, RenderInputs
from tests.review_ui.admin_browser import post_form
from tests.review_ui.sample_data import make_fleet, make_job

UPRIGHT = {"display_width": 1080, "display_height": 1920, "vertical": True}


def add_job(fake_orchestrator, job_id, **changes):
    fake_orchestrator.add_job(make_job(job_id, **changes))


# --- The job page ---


def test_a_vertical_video_is_marked_and_its_shape_is_said(ui, fake_orchestrator):
    add_job(fake_orchestrator, "job-upright", **UPRIGHT)

    html = ui.get("/review/jobs/job-upright").text

    # The header marker and the sentence in the "Imagem e som" box.
    assert ">vertical</span>" in html
    assert "Vídeo na vertical (1080×1920)." in html
    # The box carries the size for its script, and "Enquadramento" shows
    # with the default "Desfocado" chosen.
    assert 'data-display-width="1080" data-display-height="1920"' in html
    assert re.search(r'<div class="framing" id="framing-box">', html)
    assert 'name="framing" value="blurred" checked' in html
    # The crop band's slider only shows for "Cortar".
    assert re.search(r'id="crop-position-box" hidden', html)


def test_a_16_9_video_hides_enquadramento(ui, fake_orchestrator):
    add_job(fake_orchestrator, "job-wide", display_width=1920, display_height=1080, vertical=False)

    html = ui.get("/review/jobs/job-wide").text

    assert ">vertical</span>" not in html
    assert "Vídeo na horizontal, 16:9 (1920×1080)." in html
    assert re.search(r'<div class="framing" id="framing-box" hidden>', html)


def test_saved_options_are_shown_and_sem_audio_mutes_the_player(ui, fake_orchestrator):
    add_job(
        fake_orchestrator,
        "job-options",
        **UPRIGHT,
        rotation=270,
        framing=Framing.CROP,
        crop_position=0.25,
        no_audio=True,
    )

    html = ui.get("/review/jobs/job-options").text

    assert '<option value="270" selected>90° para a esquerda</option>' in html
    assert 'name="framing" value="crop" checked' in html
    assert 'id="crop_position" name="crop_position" type="range"' in html
    assert 'value="0.25"' in html
    assert re.search(r'id="crop-position-box">', html)  # not hidden for "Cortar"
    assert 'name="no_audio" checked' in html
    # The player starts muted: the reviewer hears what goes out (nothing).
    assert re.search(r"<video id=\"player\"[^>]*\bmuted\b", html)
    # Turned a quarter, the upright video is horizontal: the sentence follows.
    assert "Vídeo na horizontal, 16:9 (1920×1080)." in html


def test_the_options_go_to_the_orchestrator_with_the_review(ui, fake_orchestrator):
    add_job(fake_orchestrator, "job-send", **UPRIGHT)

    response = ui.patch(
        "/review/api/jobs/job-send/review",
        json={
            "expected_version": 3,
            "action": "save",
            "rotation": 90,
            "framing": "black_bars",
            "crop_position": 0.5,
            "no_audio": True,
        },
    )

    assert response.status_code == 200, response.text
    job = fake_orchestrator.jobs["job-send"]
    assert (job.rotation, job.framing, job.no_audio) == (90, Framing.BLACK_BARS, True)


def test_a_turn_that_is_not_allowed_is_refused_before_the_orchestrator(ui, fake_orchestrator):
    add_job(fake_orchestrator, "job-bad", **UPRIGHT)
    response = ui.patch(
        "/review/api/jobs/job-bad/review",
        json={"expected_version": 3, "action": "save", "rotation": 45},
    )
    assert response.status_code == 400
    assert fake_orchestrator.jobs["job-bad"].rotation == 0


def test_the_queue_marks_vertical_videos(ui, fake_orchestrator):
    add_job(fake_orchestrator, "job-upright", **UPRIGHT)

    html = ui.get("/review/").text

    row = re.search(r'data-job-id="job-upright".*?</a>', html, re.S).group(0)
    assert ">vertical</span>" in row
    other = re.search(r'data-job-id="job-awaiting-1".*?</a>', html, re.S).group(0)
    assert ">vertical</span>" not in other


@pytest.mark.parametrize(
    ("size", "rotation", "sentence", "applies"),
    [
        ((1080, 1920), 0, "Vídeo na vertical (1080×1920).", True),
        ((1080, 1920), 90, "Vídeo na horizontal, 16:9 (1920×1080).", False),
        ((1920, 1080), 0, "Vídeo na horizontal, 16:9 (1920×1080).", False),
        ((1920, 1080), 270, "Vídeo na vertical (1080×1920).", True),
        (
            (1440, 1080),
            0,
            "Vídeo na horizontal, mas não 16:9 (1440×1080): as bordas seguem o Enquadramento.",
            True,
        ),
        ((None, None), 0, SCRIPT_TEXT["script.orientation.unknown"], True),
    ],
)
def test_orientation_sentence_and_whether_enquadramento_applies(size, rotation, sentence, applies):
    assert orientation_text(*size, rotation) == sentence
    assert framing_applies(*size, rotation) is applies


# --- A clip whose options changed ---


def test_option_differences_are_put_in_words():
    clip = RenderInputs(rotation=0, framing=Framing.BLURRED, no_audio=False)
    now = RenderInputs(rotation=90, framing=Framing.CROP, crop_position=0.3, no_audio=True)

    assert difference_sentence(RenderInputField.ROTATION, clip, now) == (
        "O clipe no ar usa “Não girar” em Girar; a revisão agora diz “90° para a direita”."
    )
    assert difference_sentence(RenderInputField.FRAMING, clip, now) == (
        "O clipe no ar usa o enquadramento “desfocado”; a revisão agora diz “cortar, faixa a 30%”."
    )
    assert difference_sentence(RenderInputField.AUDIO, clip, now) == (
        "O clipe no ar tem som; a revisão agora diz “Sem áudio”."
    )
    # A clip rendered before the options (the narrow strip) had no framing.
    old_clip = RenderInputs()
    assert "“nenhum (16:9)”" in difference_sentence(RenderInputField.FRAMING, old_clip, now)
    assert difference_sentence(RenderInputField.AUDIO, now, clip) == (
        "O clipe no ar está sem som; a revisão agora quer o som."
    )


# --- The waiting sentence ---


def test_the_cannot_render_reason_matches_the_orchestrator_and_is_in_pt_br():
    # The UI recognises this exact sentence; if the orchestrator rewords it,
    # the UI would fall back to showing the English.
    assert NO_HOLDER_CAN_RENDER_REASON == NO_HOLDER_CAN_RENDER
    described = describe_waiting_reason(NO_HOLDER_CAN_RENDER)
    assert described.text.startswith("Esperando um PC com o original que saiba fazer")
    assert described.detail is None


# --- Frota ---


def test_frota_says_what_each_worker_version_can_render():
    fleet = make_fleet()
    worker = fleet.workers[0].worker
    old = worker.model_copy(
        update={"capabilities": worker.capabilities.model_copy(update={"render_features": []})}
    )
    new = worker.model_copy(
        update={
            "capabilities": worker.capabilities.model_copy(
                update={"render_features": ["framing", "no_audio", "pass_results"]}
            )
        }
    )
    assert render_features_text(old) == ("Versão antiga: não faz vídeo na vertical nem “Sem áudio”")
    # A feature this UI has no words for yet (a newer worker) is shown as is.
    assert render_features_text(new) == "Sabe fazer: vertical e girar, sem áudio, pass_results"


def test_the_fleet_page_shows_it(ui):
    html = ui.get("/review/fleet").text
    # The sample fleet's workers declare nothing (as 0.3.0 did). The studio
    # renders nothing, so its row has no such line (WorkerRow.render_features).
    assert "Versão antiga: não faz vídeo na vertical" in html


# --- Configurações (against the real orchestrator) ---


def test_the_settings_page_edits_the_reply_to_vertical_videos(admin, harness):
    html = admin.get("/review/admin/settings").text
    assert '<textarea id="vertical_video_message" name="vertical_video_message"' in html
    assert "📱 Vídeo na vertical. Da próxima vez, grave na horizontal, por favor." in html

    # Saving the form (every field, as the browser sends it) with an empty
    # text turns the reply off.
    page = html
    fields = dict(re.findall(r'<input id="(\w+)" name="\w+"[^>]*?value="([^"]*)"', page))
    fields.update(
        intake_mode="open",
        clip_team_name_style="short",
        clip_school_name_style="short",
        welcome_message=harness.context.admin_settings.current.welcome_message,
        vertical_video_message="",
    )
    post_form(admin, "/review/admin/settings", **fields)
    assert harness.context.admin_settings.current.vertical_video_message == ""


# --- The page script follows the same rules (Node) ---

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const r = await import(input.module);
console.log(JSON.stringify({
  sixteenNine: input.sizes.map(([w, h]) => r.fillsFrameLikeBefore(w, h)),
  turned: input.turns.map(([w, h, rotation]) => r.turnedSize(w, h, rotation)),
  orientation: input.sizes.map(([w, h]) => r.orientationKey(w, h)),
  band: input.bands.map(([w, h, p]) => r.cropBandFractions(w, h, p)),
}));
"""

SIZES = [
    (1920, 1080),
    (1280, 720),
    (854, 480),
    (1920, 1088),
    (1440, 1080),
    (2560, 1080),
    (1080, 1920),
]
TURNS = [(1920, 1080, 0), (1920, 1080, 90), (1920, 1080, 180), (1080, 1920, 270)]
BANDS = [(1080, 1920, 0.0), (1080, 1920, 0.5), (1080, 1920, 1.0), (2560, 1080, 1.0)]


@pytest.fixture(scope="module")
def js_results() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "module": (STATIC_DIR / "js" / "render_options.js").as_uri(),
        "sizes": SIZES,
        "turns": TURNS,
        "bands": BANDS,
    }
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"CASES": json.dumps(cases), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_js_16_9_rule_matches_python(js_results):
    assert js_results["sixteenNine"] == [fills_frame_like_before(w, h) for w, h in SIZES]


def test_js_turn_and_orientation_match_python(js_results):
    assert js_results["turned"] == [[1920, 1080], [1080, 1920], [1920, 1080], [1920, 1080]]
    expected = [orientation_text(w, h, 0) for w, h in SIZES]
    keys = js_results["orientation"]
    assert [SCRIPT_TEXT[key].split(" (")[0] for key in keys] == [
        sentence.split(" (")[0] for sentence in expected
    ]


def test_js_crop_band_is_where_the_worker_cuts(js_results):
    # The band the page draws, in fractions of the picture, against the
    # pixels the render cuts (shared.framing.FramePlan.crop_box).
    for (width, height, position), band in zip(BANDS, js_results["band"], strict=True):
        cut_width, cut_height, left, top = plan_frame(
            width, height, framing=Framing.CROP, crop_position=position
        ).crop_box
        assert band["width"] == pytest.approx(cut_width / width, abs=0.002)
        assert band["height"] == pytest.approx(cut_height / height, abs=0.002)
        assert band["left"] == pytest.approx(left / width, abs=0.002)
        assert band["top"] == pytest.approx(top / height, abs=0.002)
