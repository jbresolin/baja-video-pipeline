"""The admin Teams page: synced teams, corrections that survive syncs, manual cars.

The teams table is what GET /teams/{car_number} (and so the review form and
the clip overlay) resolves a car number against. It is filled from the Baja
results API (test_results_sync.py). On this page an admin can:

* correct a synced team: stored as an "override" next to the API's values,
  so the next sync does not undo it; "remove" then only drops the correction;
* add a car the API does not list (a manual team), edit it, remove it.

The fake results API (orchestrator/testing/fake_results_api.py) has the
documented 26BR example: car 1 "BAJA UFMG" (short "Baja UFMG") and car 2
"Equipe FEI Baja" (short "FEI").
"""

import pytest


def teams(harness) -> dict[int, dict]:
    listed = harness.client.get("/admin/teams", auth=harness.admin_auth).json()["teams"]
    return {team["car_number"]: team for team in listed}


def save(harness, car_number, team_name, short_name=None, school=None, school_short=None):
    body = {"team_name": team_name, "short_name": short_name, "school": school}
    if school_short is not None:
        body["school_short"] = school_short
    return harness.client.put(f"/admin/teams/{car_number}", json=body, auth=harness.admin_auth)


def sync(harness):
    response = harness.client.post("/admin/results-sync", auth=harness.admin_auth)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def synced(harness):
    """A harness whose event is 26BR, synced once (the Settings change syncs)."""
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    return harness


def test_a_manual_car_can_be_added_edited_and_removed(harness):
    # Add: PUT on a car that is not there yet.
    assert save(harness, 42, "Equipe Trovão", school="UFSC").status_code == 200
    found = harness.client.get("/teams/42", auth=harness.reviewer_auth).json()
    assert found["team_name"] == "Equipe Trovão"
    # Edit: PUT again. An empty school means "none".
    save(harness, 42, "Trovão Baja", school="  ")
    row = teams(harness)[42]
    assert (row["team_name"], row["school"], row["source"]) == ("Trovão Baja", None, "manual")
    # Remove: gone.
    assert harness.client.delete("/admin/teams/42", auth=harness.admin_auth).status_code == 204
    assert 42 not in teams(harness)
    assert harness.client.delete("/admin/teams/42", auth=harness.admin_auth).status_code == 404


def test_car_numbers_outside_0_to_9999_are_refused(harness):
    assert save(harness, 10000, "Too big").status_code == 400


def test_a_correction_of_a_synced_team_survives_the_next_sync(synced):
    row = teams(synced)[2]
    assert (row["source"], row["team_name"], row["overridden"]) == (
        "results_api",
        "Equipe FEI Baja",
        False,
    )
    # The admin corrects the short name shown on the overlay.
    save(synced, 2, "Equipe FEI Baja", short_name="FEI Baja")
    # The API renames the team meanwhile; then a sync runs.
    synced.results_api.teams_of()[1]["name"] = "FEI Baja Team"
    sync(synced)

    row = teams(synced)[2]
    # The correction is still what lookups answer...
    assert (row["team_name"], row["short_name"], row["overridden"]) == (
        "Equipe FEI Baja",
        "FEI Baja",
        True,
    )
    # ...while the API's newer value is kept next to it, for the page.
    assert row["synced_team_name"] == "FEI Baja Team"


def test_removing_a_synced_team_only_drops_the_correction(synced):
    save(synced, 1, "UFMG corrigida")
    assert synced.client.delete("/admin/teams/1", auth=synced.admin_auth).status_code == 204
    # The car is still there, with the API's values again.
    row = teams(synced)[1]
    assert (row["team_name"], row["short_name"], row["overridden"]) == (
        "BAJA UFMG",
        "Baja UFMG",
        False,
    )


def test_team_changes_are_audited(harness):
    save(harness, 42, "Equipe Trovão")
    harness.client.delete("/admin/teams/42", auth=harness.admin_auth)
    events = harness.client.get("/admin/audit?area=teams", auth=harness.admin_auth).json()["events"]
    assert [event["kind"] for event in events] == ["team_deleted", "team_saved"]
    assert events[1]["details"]["before"] is None  # a new car
    assert events[0]["details"]["after"] is None  # gone


def test_the_school_short_name_can_be_corrected_and_survives_a_sync(synced):
    # The API says "UFMG" for car 1; the admin wants "UFMG-BH" on the clips.
    assert teams(synced)[1]["school_short"] == "UFMG"
    save(synced, 1, "BAJA UFMG", school_short="UFMG-BH")
    sync(synced)
    row = teams(synced)[1]
    assert (row["school_short"], row["synced_school_short"]) == ("UFMG-BH", "UFMG")
