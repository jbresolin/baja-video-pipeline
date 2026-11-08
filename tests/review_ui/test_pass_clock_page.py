"""The review UI's side of LIVESTACK-48: the largada, "Mostrar cronômetro" and the overlay preview.

* For a prova whose configuration has a clock, the job page has a
  "Cronômetro" block: the largada (a mark like the cuts), "Mostrar
  cronômetro" ticked as the orchestrator says, and one line saying what
  will be burned in, or what is missing.
* "Pré-visualizar overlay" draws the overlay over the player; its data
  (the texts as a render gets them) is embedded in the page.
* The overlay preview uses the worker's own tokens.css and font: the
  review UI has byte-for-byte copies, and serves tokens.css scoped to the
  preview so it cannot restyle the rest of the page.
* A clip whose clock changed says so in words; the waiting sentence and
  Frota's feature name are in pt-BR.

The JavaScript (the largada field, the body sent on save, the preview's
text splitting) runs in Node at the end of this file. The clock's rule
itself is tests/shared/test_pass_clock_rule.py.

Setup (conftest.py): `ui` is a signed-in browser against a fake
orchestrator. sample_data.pass_clock_job() is car 26's second run in
Manobrabilidade: official time "66,0", largada marked at 12.345 s.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_CLOCK
from review_ui.catalog import SCRIPT_TEXT
from review_ui.clip_inputs_view import difference_sentence
from review_ui.clips_and_fleet_view import render_features_text
from review_ui.job_view import NO_HOLDER_CAN_RENDER_CLOCK_REASON, describe_waiting_reason
from review_ui.overlay_preview import (
    THEME_DIR,
    ThemeScopeError,
    scoped_tokens_css,
)
from review_ui.pass_clock_view import overlay_preview_data, pass_clock_box
from review_ui.pass_results_settings_view import trial_outcome
from review_ui.templating import STATIC_DIR
from shared.models import (
    PassClock,
    PassResultProblem,
    PassResultTestRequest,
    PassResultTestResponse,
    RenderClock,
    RenderInputField,
    RenderInputs,
)
from tests.review_ui.english_guard import english_words_in, visible_text
from tests.review_ui.sample_data import (
    T0,
    failing_fetch,
    make_fleet,
    make_job,
    pass_clock,
    pass_clock_job,
)

WORKER_THEME = Path(__file__).resolve().parents[2] / "worker" / "src" / "worker" / "overlay_theme"


# --- What the block says (pass_clock_view.py, no server) ---


def test_a_marked_largada_with_an_official_time_goes_on_the_overlay():
    box = pass_clock_box(pass_clock(), T0, result_box_shown=True)
    assert box.show is True
    assert box.largada_value == "12.345"
    assert box.status == "Na tarja: da largada até 66,0 s, e para."


def test_without_a_largada_it_asks_for_one():
    clock = pass_clock(largada_s=None, render=None)
    box = pass_clock_box(clock, T0, result_box_shown=True)
    assert box.largada_value == ""
    assert box.status == "Marque a largada para gravar o cronômetro (para em 66,0 s)."


def test_turned_off_it_says_it_will_not_be_burned_in():
    box = pass_clock_box(pass_clock(show=False, render=None), T0, result_box_shown=True)
    assert box.status == "Não será gravado (pararia em 66,0 s)."


def test_without_an_official_time_it_says_why_like_the_result_line():
    clock = pass_clock(
        official_units=None, official_text=None, render=None, problem="no_result_yet"
    )
    box = pass_clock_box(clock, T0, result_box_shown=True)
    assert box.status == (
        "Sem tempo oficial ainda: Ainda sem resultado oficial para esta passagem."
    )


def test_no_block_for_a_prova_without_a_clock_or_an_older_orchestrator():
    not_configured = PassClock(problem=PassResultProblem.NOT_CONFIGURED)
    assert pass_clock_box(not_configured, T0, result_box_shown=False) is None
    assert pass_clock_box(None, T0, result_box_shown=False) is None


def test_the_fetch_warning_shows_here_only_without_the_result_block():
    # With a result block on the page it already says the API is down.
    clock = pass_clock(fetch=failing_fetch())
    assert pass_clock_box(clock, T0, result_box_shown=True).fetch_warning is None
    alone = pass_clock_box(clock, T0, result_box_shown=False)
    assert alone.fetch_warning.startswith("API de resultados inacessível desde")


def test_the_preview_data_is_what_a_render_gets():
    data = overlay_preview_data(pass_clock_job())
    assert data == {
        "car_number": 26,
        "team_name": "Saci Baja",
        "school_name": "UNIFEI",
        "prova": "Manobrabilidade",
        "result_text": "66 s · 4 cones → 86 s",
        "clock": {"official_units": 660, "decimals": 1},
    }
    # A job without a clock or a result: just the names.
    plain = overlay_preview_data(make_job())
    assert (plain["result_text"], plain["clock"]) == (None, None)


# --- The job page ---


def test_the_job_page_has_the_clock_block_ticked_with_the_largada(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_clock_job("job-clock"))

    html = ui.get("/review/jobs/job-clock").text

    assert 'name="show_clock" checked' in html
    assert 'id="largada_s" name="largada_s"' in html
    assert 'value="12.345"' in html
    assert 'data-set-mark="largada"' in html
    assert "Na tarja: da largada até 66,0 s, e para." in html
    # The l shortcut is listed, the largada line is on the cut bar.
    assert "Marcar a largada (cronômetro)" in html
    assert 'id="largada-mark"' in html
    # And the page stays pt-BR.
    assert not english_words_in(visible_text(html))


def test_no_clock_block_for_a_job_without_a_clock(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-no-clock"))
    html = ui.get("/review/jobs/job-no-clock").text
    assert 'name="largada_s"' not in html
    assert "Marcar a largada" not in html


def test_the_preview_toggle_and_its_data_are_on_the_page(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_clock_job("job-preview"))

    html = ui.get("/review/jobs/job-preview").text

    assert 'id="overlay-preview-toggle"' in html
    assert "Pré-visualizar overlay" in html
    # The data the script draws, as JSON in the page.
    start = html.index('<script type="application/json" id="overlay-preview-data">')
    end = html.index("</script>", start)
    data = json.loads(html[html.index(">", start) + 1 : end])
    assert data["clock"] == {"official_units": 660, "decimals": 1}
    # The overlay's look: the scoped tokens and the preview's stylesheet.
    assert "/review/overlay-theme/tokens.css" in html
    assert "css/overlay_preview.css" in html


def test_the_largada_and_the_toggle_go_to_the_orchestrator(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_clock_job("job-send"))

    response = ui.patch(
        "/review/api/jobs/job-send/review",
        json={"expected_version": 3, "action": "save", "largada_s": 14.0, "show_clock": False},
    )

    assert response.status_code == 200, response.text
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-send/review")
    assert (sent.json_body["largada_s"], sent.json_body["show_clock"]) == (14.0, False)
    assert "clear_largada" not in sent.json_body


def test_removing_the_largada_goes_to_the_orchestrator(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_clock_job("job-clear"))

    response = ui.patch(
        "/review/api/jobs/job-clear/review",
        json={"expected_version": 3, "action": "save", "clear_largada": True},
    )

    assert response.status_code == 200, response.text
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-clear/review")
    assert sent.json_body["clear_largada"] is True
    assert fake_orchestrator.jobs["job-clear"].largada_s is None


# --- The overlay's look: the worker's own files ---


@pytest.mark.parametrize("name", ["tokens.css", "BebasNeue-Regular.ttf", "BebasNeue-LICENSE.txt"])
def test_the_preview_uses_byte_for_byte_copies_of_the_workers_theme(name):
    # tools/refresh_overlay_theme.py writes both; if they differ, the
    # preview no longer looks like the clip.
    assert (THEME_DIR / name).read_bytes() == (WORKER_THEME / name).read_bytes()


def test_tokens_css_is_served_scoped_to_the_preview(ui):
    response = ui.get("/review/overlay-theme/tokens.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    css = response.text
    # The declarations apply inside .overlay-preview only: the review UI's
    # own --radius and --text keep their meaning elsewhere on the page.
    assert "\n.overlay-preview {" in css
    assert "\n:root {" not in css
    assert "--panel-base" in css
    # The font, under the family name the tokens ask for.
    assert "font-family: 'Bebas Neue'" in css
    assert (
        ui.get("/review/overlay-theme/BebasNeue-Regular.ttf").content
        == (WORKER_THEME / "BebasNeue-Regular.ttf").read_bytes()
    )


def test_a_tokens_file_of_another_shape_fails_loudly():
    with pytest.raises(ThemeScopeError):
        scoped_tokens_css(".theme { --text: #fff; }")


# --- A clip whose clock changed ---

CLOCK = RenderClock(largada_s=12.345, official_units=660, decimals=1)


def test_a_clock_that_would_now_appear_is_put_in_words():
    sentence = difference_sentence(
        RenderInputField.CLOCK, RenderInputs(), RenderInputs(pass_clock=CLOCK)
    )
    assert sentence == (
        "O cronômetro agora entraria no clipe: largada em 0:12,345, parando em 66,0 s."
    )


def test_a_moved_largada_is_put_in_words():
    moved = CLOCK.model_copy(update={"largada_s": 13.0})
    sentence = difference_sentence(
        RenderInputField.CLOCK, RenderInputs(pass_clock=CLOCK), RenderInputs(pass_clock=moved)
    )
    assert sentence == (
        "O cronômetro mudou: o clipe no ar tem largada em 0:12,345, parando em 66,0 s; "
        "agora seria largada em 0:13,000, parando em 66,0 s."
    )


def test_a_clock_no_longer_shown_is_put_in_words():
    sentence = difference_sentence(
        RenderInputField.CLOCK, RenderInputs(pass_clock=CLOCK), RenderInputs()
    )
    assert sentence.endswith("a revisão agora diz para não mostrar.")


def test_the_cannot_draw_the_clock_reason_matches_the_orchestrator_and_is_pt_br():
    assert NO_HOLDER_CAN_RENDER_CLOCK_REASON == NO_HOLDER_CAN_RENDER_CLOCK
    described = describe_waiting_reason(NO_HOLDER_CAN_RENDER_CLOCK)
    assert "cronômetro" in described.text
    assert "0.6.0" in described.text
    assert described.detail is None


def test_frota_names_the_clock_feature():
    worker = make_fleet().workers[0].worker
    features = ["framing", "no_audio", "pass_result", "clock"]
    capabilities = worker.capabilities.model_copy(update={"render_features": features})
    new = worker.model_copy(update={"capabilities": capabilities})

    assert render_features_text(new) == (
        "Sabe fazer: vertical e girar, sem áudio, resultado da passagem, cronômetro"
    )


# --- The page scripts, in Node ---

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const { collectReviewBody } = await import(input.review_form);
const { largadaFieldValue } = await import(input.player);
const { resultParts, roleParts } = await import(input.preview);
function form(extra) {
  return { elements: {
    prova: { value: "Manobrabilidade" }, car_number: { value: "26" }, passagem: { value: "2" },
    cut_in_s: { value: "" }, cut_out_s: { value: "" }, show_pass: { checked: true },
    rotation: { value: "0" }, no_audio: { checked: false }, ...extra,
  } };
}
console.log(JSON.stringify({
  without: collectReviewBody(form({}), 3, "save"),
  marked: collectReviewBody(
    form({ largada_s: { value: "12.345" }, show_clock: { checked: true } }), 3, "save"),
  cleared: collectReviewBody(
    form({ largada_s: { value: "" }, show_clock: { checked: false } }), 3, "save"),
  largada: [largadaFieldValue(2.0666666666666664), largadaFieldValue(2.033), largadaFieldValue(0)],
  result: resultParts("58 s · 8 cones, 2 saídas de pista → 118 s"),
  role: roleParts({ prova: "Manobrabilidade", passagem: 2, showPass: true, school: "Unifei" }),
  roleHidden: roleParts({ prova: "Enduro", passagem: 2, showPass: false, school: "" }),
}));
"""


@pytest.fixture(scope="module")
def js() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "review_form": (STATIC_DIR / "js" / "review_form.js").as_uri(),
        "player": (STATIC_DIR / "js" / "player.js").as_uri(),
        "preview": (STATIC_DIR / "js" / "overlay_preview.js").as_uri(),
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


def test_js_leaves_the_clock_out_when_the_block_is_not_on_the_page(js):
    # Left out = "keep": a save of another prova's job never touches the clock.
    assert "largada_s" not in js["without"]
    assert "clear_largada" not in js["without"]
    assert "show_clock" not in js["without"]


def test_js_sends_the_largada_and_the_toggle(js):
    assert (js["marked"]["largada_s"], js["marked"]["show_clock"]) == (12.345, True)
    assert "clear_largada" not in js["marked"]


def test_js_sends_an_empty_largada_as_a_removal(js):
    assert js["cleared"]["clear_largada"] is True
    assert "largada_s" not in js["cleared"]
    assert js["cleared"]["show_clock"] is False


def test_the_largada_field_is_rounded_down_to_the_millisecond(js):
    # A frame at 2.0666… s is marked 2.066, not 2.067: the frame the
    # reviewer saw is then at or after the largada, so it shows 0,0.
    assert js["largada"] == ["2.066", "2.033", "0.000"]


def test_the_preview_splits_the_result_like_the_worker(js):
    assert js["result"] == [
        {"text": "58 S", "kind": "words"},
        {"text": "·", "kind": "separator"},
        {"text": "8 CONES, 2 SAÍDAS DE PISTA", "kind": "words"},
        {"text": "→", "kind": "arrow"},
        {"text": "118 S", "kind": "words"},
    ]


def test_the_preview_role_pill_follows_the_form(js):
    assert js["role"] == [
        {"text": "MANOBRABILIDADE", "muted": False},
        {"text": "PASSAGEM 2", "muted": False},
        {"text": "UNIFEI", "muted": True},
    ]
    # "Mostrar o número da passagem" off and no school: the prova alone.
    assert js["roleHidden"] == [{"text": "ENDURO", "muted": False}]


# --- "Testar" on the Settings page ---


def trial(**answer_fields):
    request = PassResultTestRequest(prova_id="MAN", car_number=26, passagem=2)
    return trial_outcome(request, PassResultTestResponse(**answer_fields), T0, unsaved=False)


def test_testar_says_where_the_clock_stops():
    outcome = trial(text="66 s · 4 cones → 86 s", clock_configured=True, clock_text="66,0")
    assert str(outcome.clock) == "Cronômetro para em <strong>66,0 s</strong>."


def test_testar_says_why_the_clock_has_no_time():
    outcome = trial(clock_configured=True, clock_problem="no_result_yet")
    # A prova with only a clock: no complaint about a missing line...
    assert outcome.problem is None
    # ...and the clock's own reason.
    assert outcome.clock == "Cronômetro: Ainda sem resultado oficial para esta passagem."


def test_testar_without_a_clock_says_nothing_about_one():
    assert trial(text="59 s").clock is None
