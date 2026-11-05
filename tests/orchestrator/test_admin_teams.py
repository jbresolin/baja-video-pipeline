"""The admin Teams page: the teams table with inline edit, add, delete and a CSV upload.

The table is what GET /teams/{car_number} (and so the review form's team
name) asks first. A CSV upload either REPLACES the whole table or MERGES
into it; the operator picks.
"""

CSV = "car_number,team_name,school\n42,Equipe Trovão,UFSC\n7,Mangue Baja,UFPE\n"


def teams(harness) -> dict[int, dict]:
    listed = harness.client.get("/admin/teams", auth=harness.admin_auth).json()["teams"]
    return {team["car_number"]: team for team in listed}


def save(harness, car_number, team_name, school=None):
    return harness.client.put(
        f"/admin/teams/{car_number}",
        json={"team_name": team_name, "school": school},
        auth=harness.admin_auth,
    )


def upload(harness, csv_text, mode):
    return harness.client.post(
        "/admin/teams/import", json={"csv_text": csv_text, "mode": mode}, auth=harness.admin_auth
    )


def test_add_edit_and_delete_one_car(harness):
    # Add: PUT on a car that is not there yet.
    assert save(harness, 42, "Equipe Trovão", "UFSC").status_code == 200
    # The review form now finds it.
    found = harness.client.get("/teams/42", auth=harness.reviewer_auth).json()
    assert found["team_name"] == "Equipe Trovão"
    # Inline edit: PUT again with new values. An empty school means "none".
    save(harness, 42, "Trovão Baja", "  ")
    assert teams(harness)[42] == {"car_number": 42, "team_name": "Trovão Baja", "school": None}
    # Delete: gone from the table (and the review form, with no Baja app).
    assert harness.client.delete("/admin/teams/42", auth=harness.admin_auth).status_code == 204
    assert 42 not in teams(harness)
    # Deleting it again: 404.
    assert harness.client.delete("/admin/teams/42", auth=harness.admin_auth).status_code == 404


def test_car_numbers_outside_0_to_9999_are_refused(harness):
    assert save(harness, 10000, "Too big").status_code == 400


def test_replace_makes_the_table_exactly_the_file(harness):
    save(harness, 99, "Old team")
    answer = upload(harness, CSV, "replace").json()
    assert answer == {"imported": 2, "problems": [], "total_teams": 2}
    assert sorted(teams(harness)) == [7, 42]  # car 99 is gone


def test_merge_adds_and_updates_but_keeps_cars_missing_from_the_file(harness):
    save(harness, 99, "Old team")
    save(harness, 42, "Name to be replaced")
    answer = upload(harness, CSV, "merge").json()
    assert answer["total_teams"] == 3
    assert teams(harness)[99]["team_name"] == "Old team"
    assert teams(harness)[42]["team_name"] == "Equipe Trovão"


def test_bad_lines_are_skipped_and_reported(harness):
    answer = upload(harness, "42,Trovão,\nabc,No number,\n7,\n", "merge").json()
    assert answer["imported"] == 1
    assert answer["problems"] == [
        "line 2: car_number 'abc' is not a number from 0 to 9999",
        "line 3: needs car_number,team_name",
    ]


def test_a_file_with_no_valid_line_never_empties_the_table(harness):
    # Uploading the wrong file in "replace" mode must not wipe the teams.
    save(harness, 42, "Equipe Trovão")
    answer = upload(harness, "this is not,a teams file\n", "replace").json()
    assert answer["imported"] == 0
    assert sorted(teams(harness)) == [42]


def test_team_changes_are_audited(harness):
    save(harness, 42, "Equipe Trovão")
    upload(harness, CSV, "replace")
    events = harness.client.get("/admin/audit?area=teams", auth=harness.admin_auth).json()["events"]
    assert [event["kind"] for event in events] == ["teams_imported", "team_saved"]
    assert events[0]["details"]["after"]["mode"] == "replace"
    assert events[1]["details"]["before"] is None  # a new car
