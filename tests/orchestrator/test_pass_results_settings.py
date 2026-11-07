"""The Settings page's pass results: the configuration, "Testar", and fetching (LIVESTACK-47).

Everything goes through the orchestrator's HTTP API as the review UI
does, against a fake results API that serves the real answer of the 2026
Manobrabilidade (tests/fixtures/results_api/). The background loop's pass
(poll_due_tables) is called directly after moving the test clock, so the
"every 30 s" schedule is checked without waiting.
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from tests.orchestrator.pass_results_data import (
    MANOBRABILIDADE_CONFIG_TEXT,
    RESULTADO_ID,
    car_row,
    job_in_review,
    save_config,
    set_up_manobrabilidade,
)

TABLE_PATH = "/resultados/resultado.php"


def settings_block(harness) -> dict:
    response = harness.client.get("/admin/pass-results", auth=harness.admin_auth)
    assert response.status_code == 200, response.text
    return response.json()


def table_requests(harness) -> list[str]:
    """The query strings of every resultado.php request the fake API received."""
    return [query for path, query in harness.results_api.requests if path == TABLE_PATH]


# --- The configuration ---


def test_a_saved_configuration_is_kept_as_typed_and_listed_per_prova(harness):
    # Setup: event 26BR synced (it lists Manobrabilidade, API id "MAN"),
    # and the brief's configuration saved.
    set_up_manobrabilidade(harness)

    block = settings_block(harness)

    # The text comes back exactly as typed (spacing included), and the
    # prova is matched to our synced "Manobrabilidade" and its table.
    assert block["config_text"] == MANOBRABILIDADE_CONFIG_TEXT
    [prova] = block["provas"]
    assert (prova["prova_id"], prova["prova_name"], prova["resultado_id"]) == (
        "MAN",
        "Manobrabilidade",
        "26BR_MAN",
    )
    # No job needs it yet, so it is not being fetched.
    assert prova["polling"] is False


def test_a_bad_configuration_is_refused_with_the_path_and_the_old_one_stays(harness):
    set_up_manobrabilidade(harness)

    seven_decimals = MANOBRABILIDADE_CONFIG_TEXT.replace('"decimals": 0', '"decimals": 7')
    response = save_config(harness, seven_decimals)

    # 422 with the place and the problem, for the pt-BR message...
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["path"] == "provas.MAN.result.decimals"
    assert details["problem"] == "out_of_range"
    # ...and the configuration in force did not change.
    assert settings_block(harness)["config_text"] == MANOBRABILIDADE_CONFIG_TEXT


def test_every_change_is_in_the_audit_with_before_and_after(harness):
    set_up_manobrabilidade(harness)
    save_config(harness, "")

    events = harness.client.get(
        "/admin/audit", params={"area": "settings"}, auth=harness.admin_auth
    ).json()["events"]
    changes = [event for event in events if event["kind"] == "pass_results_config_updated"]
    # Newest first: turned off, then the first save.
    assert changes[0]["details"] == {
        "before": {"config_text": MANOBRABILIDADE_CONFIG_TEXT},
        "after": {"config_text": ""},
    }
    assert changes[1]["details"]["before"] == {"config_text": ""}


def test_only_admins_see_or_change_it(harness):
    assert harness.client.get("/admin/pass-results", auth=harness.reviewer_auth).status_code == 403
    response = harness.client.put(
        "/admin/pass-results/config", json={"config_text": ""}, auth=harness.reviewer_auth
    )
    assert response.status_code == 403


# --- "Testar" ---


def test_testar_fetches_live_data_and_shows_the_text_and_the_cars_row(harness):
    set_up_manobrabilidade(harness)

    response = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": "MAN", "car_number": 2, "passagem": 1},
        auth=harness.admin_auth,
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["text"] == "58 s · 8 cones, 2 saídas de pista → 118 s"
    assert answer["resultado_id"] == "26BR_MAN"
    # It really asked the API, with exactly the documented parameters.
    assert table_requests(harness) == ["evt=26BR&id=26BR_MAN&display=1"]
    assert answer["fetch"]["last_attempt_ok"] is True
    # The car's row as the API sent it, so the admin sees what it came from.
    [row] = answer["cells"]
    tempo = next(cell for cell in row if cell["column"] == "Tempo")
    assert tempo["raw"] == ["58 (8 /2 )", "57 (3 )"]


def test_testar_can_try_a_configuration_before_it_is_saved(harness):
    set_up_manobrabilidade(harness)
    unsaved = MANOBRABILIDADE_CONFIG_TEXT.replace('"penalties": true', '"penalties": false')

    answer = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": "MAN", "car_number": 2, "passagem": 1, "config_text": unsaved},
        auth=harness.admin_auth,
    ).json()

    # The unsaved text was used (no penalties), the saved one is unchanged.
    assert answer["text"] == "58 s → 118 s"
    assert settings_block(harness)["config_text"] == MANOBRABILIDADE_CONFIG_TEXT


def test_testar_reuses_an_answer_from_the_last_seconds(harness):
    # Clicking "Testar" twice in a row asks the API once: it caches 5 s anyway.
    set_up_manobrabilidade(harness)
    body = {"prova_id": "MAN", "car_number": 26, "passagem": 1}
    harness.client.post("/admin/pass-results/test", json=body, auth=harness.admin_auth)
    harness.client.post("/admin/pass-results/test", json=body, auth=harness.admin_auth)
    assert len(table_requests(harness)) == 1
    harness.clock.advance(6)
    harness.client.post("/admin/pass-results/test", json=body, auth=harness.admin_auth)
    assert len(table_requests(harness)) == 2


def test_testar_says_why_for_a_prova_that_is_not_configured(harness):
    set_up_manobrabilidade(harness)
    answer = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": "END", "car_number": 26, "passagem": 1},
        auth=harness.admin_auth,
    ).json()
    assert (answer["text"], answer["problem"]) == (None, "not_configured")
    assert table_requests(harness) == []


# --- Fetching on a schedule ---


def test_only_provas_with_jobs_that_need_results_are_fetched(harness):
    # Setup: configured, but no job of the prova yet.
    set_up_manobrabilidade(harness)
    assert poll_due_tables(harness.context) == []

    # A Manobrabilidade job enters review: from now on the table is fetched.
    job_in_review(harness)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]
    assert settings_block(harness)["provas"][0]["polling"] is True


def test_the_table_is_fetched_every_30_seconds_while_the_event_runs(harness):
    set_up_manobrabilidade(harness)
    job_in_review(harness)
    poll_due_tables(harness.context)

    # 29 s later it is not due; at 30 s it is. (The API caches 5 s, so
    # this stays far below its 120 requests a minute.)
    harness.clock.advance(29)
    assert poll_due_tables(harness.context) == []
    harness.clock.advance(1)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]
    assert len(table_requests(harness)) == 2


def test_once_the_event_is_finished_one_last_fetch_after_300_s_then_no_more(harness):
    # A finished event's answers are cached 300 s (Cache-Control max-age).
    set_up_manobrabilidade(harness)
    harness.results_api.table_max_age_s = 300
    job_in_review(harness)
    poll_due_tables(harness.context)

    # The last fetch waits for the API's cache to expire, so it is fresh.
    harness.clock.advance(60)
    assert poll_due_tables(harness.context) == []
    harness.clock.advance(240)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]

    # Then nothing more, however long the job stays in the queue.
    harness.clock.advance(300)
    assert poll_due_tables(harness.context) == []
    harness.clock.advance(24 * 3600)
    assert poll_due_tables(harness.context) == []
    assert len(table_requests(harness)) == 2
    prova = settings_block(harness)["provas"][0]
    assert (prova["polling"], prova["fetch"]["event_finished"]) == (False, True)


def test_an_answer_saying_the_event_runs_again_starts_fetching_again(harness):
    # Setup: the event finished and the last fetch was done.
    set_up_manobrabilidade(harness)
    harness.results_api.table_max_age_s = 300
    job_in_review(harness)
    poll_due_tables(harness.context)
    harness.clock.advance(300)
    poll_due_tables(harness.context)

    # The organizers reopen the event; "Testar" is what still asks the API.
    harness.results_api.table_max_age_s = 5
    harness.clock.advance(300)
    body = {"prova_id": "MAN", "car_number": 26, "passagem": 1}
    harness.client.post("/admin/pass-results/test", json=body, auth=harness.admin_auth)

    harness.clock.advance(30)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]
    assert settings_block(harness)["provas"][0]["fetch"]["event_finished"] is False


def test_a_failure_after_the_event_finished_does_not_count_as_the_last_fetch(harness):
    set_up_manobrabilidade(harness)
    harness.results_api.table_max_age_s = 300
    job_in_review(harness)
    poll_due_tables(harness.context)

    # The last fetch fails: it is tried again 30 s later until one works.
    harness.results_api.unreachable = True
    harness.clock.advance(300)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]
    harness.results_api.unreachable = False
    harness.clock.advance(30)
    assert poll_due_tables(harness.context) == [RESULTADO_ID]
    harness.clock.advance(300)
    assert poll_due_tables(harness.context) == []


def test_a_failure_keeps_the_last_good_answer_and_says_since_when(harness):
    # Setup: one good fetch; car 26's passagem 2 reads "66 s · 4 cones → 86 s".
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)
    assert harness.job(job_id)["pass_result"]["text"] == "66 s · 4 cones → 86 s"
    good_at = harness.clock.now()

    # What it does: the API goes down for two fetches.
    harness.results_api.unreachable = True
    harness.clock.advance(30)
    poll_due_tables(harness.context)
    first_failure = harness.clock.now()
    harness.clock.advance(30)
    poll_due_tables(harness.context)

    # The text is still there (nothing on air is blanked), marked stale,
    # with when it last worked and since when it fails.
    result = harness.job(job_id)["pass_result"]
    assert result["text"] == "66 s · 4 cones → 86 s"
    fetch = result["fetch"]
    assert fetch["stale"] is True
    assert fetch["last_attempt_ok"] is False
    assert "unreachable" in fetch["last_error"]
    assert fetch["last_success_at"] == good_at.isoformat().replace("+00:00", "Z")
    assert fetch["failing_since"] == first_failure.isoformat().replace("+00:00", "Z")
    # The Settings page shows the same next to the configuration.
    assert settings_block(harness)["provas"][0]["fetch"]["stale"] is True

    # When the API is back, the next fetch clears it all.
    harness.results_api.unreachable = False
    harness.clock.advance(30)
    poll_due_tables(harness.context)
    fetch = harness.job(job_id)["pass_result"]["fetch"]
    assert (fetch["stale"], fetch["failing_since"], fetch["last_error"]) == (False, None, None)


def test_a_result_corrected_by_the_judges_is_picked_up_at_the_next_fetch(harness):
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)

    # The judges correct car 26's second run: 5 cones, corrected 91.
    table = harness.results_api.tables[RESULTADO_ID]
    car_row(table, 26)["cells"][3]["display"] = "59<br /> 66 (5<img class='cone'>)"
    car_row(table, 26)["cells"][4]["raw"][1] = 91
    harness.clock.advance(30)
    poll_due_tables(harness.context)

    assert harness.job(job_id)["pass_result"]["text"] == "66 s · 5 cones → 91 s"


def test_nothing_is_fetched_without_an_event_or_a_configuration(harness):
    job_in_review(harness)
    assert poll_due_tables(harness.context) == []
    assert table_requests(harness) == []
