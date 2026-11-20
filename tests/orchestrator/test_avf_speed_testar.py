""" "Testar" and the Settings status for a prova with a top speed (LIVESTACK-67).

What this module is for: on the Settings page, the admin checks a
configuration with "Testar" (a prova, a car, a passagem). For
"Aceleração e Velocidade" the answer now also says the speed the pill
would show ("Velocidade: 37,2 km/h"), read from the speed's own table,
which "Testar" fetches too. The status list next to the configuration
shows the speed's table with its own fetch status.

What these tests prove: "Testar" fetches both tables and answers the
clock's stop and the speed for run 1, run 2, and a run with no speed; a
prova without a speed block answers no speed; the Settings status names
the speed table.

What they do not cover: how the page writes it in pt-BR (the review UI's
tests, test_avf_speed_settings_page.py).
"""

import pytest

from tests.orchestrator.avf_data import CLOCK_TABLE, SPEED_TABLE, set_up_avf
from tests.orchestrator.pass_results_data import set_up_manobrabilidade


def click_testar(harness, prova_id: str, car_number: int, passagem: int) -> dict:
    """POST /admin/pass-results/test as the admin; the answer's JSON."""
    response = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": prova_id, "car_number": car_number, "passagem": passagem},
        auth=harness.admin_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


# Each row: car, passagem, the clock's stop, the speed, and why the row exists.
TESTAR_CASES = [
    # Run 1 of each table: 4.354 s (rounded down to 4,35) and 37.18 km/h.
    pytest.param(1, 1, "4,35", "37,2", id="run-1"),
    # Run 2 pairs with run 2: 4.328 s and 36.49 km/h.
    pytest.param(1, 2, "4,32", "36,5", id="run-2"),
    # Car 2's second run was never run: neither a stop nor a speed.
    pytest.param(2, 2, None, None, id="no-speed"),
]


@pytest.mark.parametrize(("car", "passagem", "clock", "speed"), TESTAR_CASES)
def test_testar_says_the_clock_and_the_speed_of_the_run(harness, car, passagem, clock, speed):
    # Arrange: AVF configured, both tables served by the fake results API.
    set_up_avf(harness)

    # Act: the admin clicks "Testar" for this car and passagem.
    answer = click_testar(harness, "AVF", car, passagem)

    # Assert: the clock's stop, and the speed with its unit, from its own table.
    assert answer["clock_text"] == clock, "the clock stop must come from the ACE table"
    assert answer["speed_configured"] is True, "AVF has a speed block"
    assert answer["speed_resultado_id"] == SPEED_TABLE, "the speed comes from 26BR_VEL"
    assert answer["speed_text"] == speed, "the speed must come from the same run of VEL"
    assert answer["speed_unit"] == "km/h", "with the configured unit"


def test_testar_for_a_run_with_no_speed_says_why(harness):
    # Arrange: AVF configured.
    set_up_avf(harness)

    # Act: "Testar" for car 2's second run ("-").
    answer = click_testar(harness, "AVF", 2, 2)

    # Assert: no speed, and the reason the page writes in pt-BR.
    assert answer["speed_text"] is None, "a '-' run has no speed"
    assert answer["speed_problem"] == "no_result_yet", "the reason must be 'no result yet'"


def test_testar_fetches_both_tables(harness):
    # Arrange: AVF configured, nothing fetched yet.
    set_up_avf(harness)

    # Act: "Testar".
    click_testar(harness, "AVF", 1, 1)

    # Assert: the fake API was asked for both tables.
    asked = [query for path, query in harness.results_api.requests if "resultado" in path]
    assert f"evt=26BR&id={CLOCK_TABLE}&display=1" in asked, "the clock's table must be fetched"
    assert f"evt=26BR&id={SPEED_TABLE}&display=1" in asked, "and the speed's table"


def test_testar_says_when_the_speed_table_cannot_be_fetched(harness):
    # Arrange: AVF configured, but the speed table not published yet.
    set_up_avf(harness, serve_speed=False)

    # Act: "Testar".
    answer = click_testar(harness, "AVF", 1, 1)

    # Assert: the clock still works; the speed says its table was never fetched.
    assert answer["clock_text"] == "4,35", "the clock does not depend on the speed table"
    assert answer["speed_text"] is None, "no table, no speed"
    assert answer["speed_problem"] == "not_fetched", "the page says the table is not fetched"
    assert answer["speed_fetch"]["last_attempt_ok"] is False, "the failed try is reported"


def test_a_prova_without_a_speed_block_answers_no_speed(harness):
    # Arrange: Manobrabilidade, which has a clock but no speed.
    set_up_manobrabilidade(harness)

    # Act: "Testar" car 26's second run.
    answer = click_testar(harness, "MAN", 26, 2)

    # Assert: nothing about a speed.
    assert answer["speed_configured"] is False, "no speed block, no speed"
    assert answer["speed_text"] is None, "and no speed text"


def test_the_settings_status_lists_the_speed_table(harness):
    # Arrange: AVF configured and one "Testar", so both tables were fetched.
    set_up_avf(harness)
    click_testar(harness, "AVF", 1, 1)

    # Act: open the Settings block.
    response = harness.client.get("/admin/pass-results", auth=harness.admin_auth)

    # Assert: the AVF row names both tables, each with its fetch status.
    assert response.status_code == 200, response.text
    [avf] = response.json()["provas"]
    assert avf["resultado_id"] == CLOCK_TABLE, "the prova's own table"
    assert avf["speed_resultado_id"] == SPEED_TABLE, "and the speed's table"
    assert avf["speed_fetch"]["last_attempt_ok"] is True, "with its own fetch status"
