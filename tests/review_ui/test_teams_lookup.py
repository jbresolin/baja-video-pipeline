"""Tests for the team lookup the job page does as the car number is typed.

GET /review/api/teams/{car} relays to the orchestrator's GET /teams/{car},
which looks in its teams table (synced from the results API). The fake
knows car 42 (Equipe Cerrado, UnB) and car 7 (Equipe Ipê, no school).
"""


def test_known_car_returns_the_team(ui, fake_orchestrator):
    response = ui.get("/review/api/teams/42")

    assert response.status_code == 200
    assert response.json() == {
        "car_number": 42,
        "team_name": "Equipe Cerrado",
        "school": "UnB",
        "short_name": None,
    }
    # It really asked the orchestrator, for that car.
    assert len(fake_orchestrator.requests_to("GET", "/teams/42")) == 1


def test_unknown_car_is_a_404_with_a_clear_message(ui):
    response = ui.get("/review/api/teams/999")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "999" in error["message"]


def test_an_orchestrator_error_is_passed_on(ui, fake_orchestrator):
    # Setup: the orchestrator answers 503 (it is restarting, say).
    fake_orchestrator.teams_upstream_down = True

    response = ui.get("/review/api/teams/42")

    # Passed on as-is, so the page can say "lookup unavailable" instead of
    # "unknown car" (the reviewer should not doubt a correct number).
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_error"


def test_car_number_out_of_range_is_refused_locally(ui, fake_orchestrator):
    # Car numbers are 0–9999 in the API; the UI checks before calling.
    response = ui.get("/review/api/teams/12345")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert fake_orchestrator.requests == []


def test_car_number_must_be_a_number(ui):
    response = ui.get("/review/api/teams/abc")

    assert response.status_code == 400
