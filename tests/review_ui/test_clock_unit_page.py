"""The clock's unit in the job page's and Settings page's sentences (LIVESTACK-53).

The clip's clock now reads "66,0 s": the unit comes from the prova's
`clock` block in the configuration (default "s"). The sentences about the
clock say the official time with that unit, so a prova configured with
another unit (or none) reads right. A clock recorded before the unit
existed (an old clip) has none on record; its number meant seconds.

Plain functions over the API models (no server), with sample_data's
car 26 second Manobrabilidade run: official time 66,0, largada marked.
"""

from review_ui.clip_inputs_view import difference_sentence
from review_ui.pass_clock_view import pass_clock_box
from review_ui.pass_results_settings_view import trial_clock
from shared.models import PassResultTestResponse, RenderClock, RenderInputField, RenderInputs
from shared.pass_clock import with_unit
from tests.review_ui.sample_data import T0, pass_clock


def status_for(**changes) -> str:
    return pass_clock_box(pass_clock(**changes), T0, result_box_shown=True).status


def test_the_status_says_the_official_time_with_the_configured_unit():
    assert status_for() == "Na tarja: da largada até 66,0 s, e para."
    assert status_for(unit="seg") == "Na tarja: da largada até 66,0 seg, e para."
    # An empty unit in the configuration: just the number.
    assert status_for(unit="") == "Na tarja: da largada até 66,0, e para."


def test_a_clock_recorded_without_a_unit_reads_as_seconds():
    # None = recorded before 2.10.0; "" = configured without a unit.
    assert with_unit("66,0", None) == "66,0 s"
    assert with_unit("66,0", "") == "66,0"


def test_the_outdated_sentence_names_each_clock_with_its_unit():
    # An old clip (no unit on record) next to a render now with "seg".
    old = RenderClock(largada_s=12.345, official_units=660, decimals=1)
    now = RenderClock(largada_s=12.345, official_units=670, decimals=1, unit="seg")
    clip_inputs = RenderInputs(pass_clock=old, reported_by_worker=True)
    now_inputs = RenderInputs(pass_clock=now)

    sentence = difference_sentence(RenderInputField.CLOCK, clip_inputs, now_inputs)

    assert sentence == (
        "O cronômetro mudou: o clipe no ar tem largada em 0:12,345, parando em 66,0 s; "
        "agora seria largada em 0:12,345, parando em 67,0 seg."
    )


def test_testar_says_where_the_clock_stops_with_its_unit():
    answer = PassResultTestResponse(
        ok=True, clock_configured=True, clock_text="66,0", clock_unit="seg"
    )
    assert str(trial_clock(answer)) == "Cronômetro para em <strong>66,0 seg</strong>."
