"""Tests for the team lookup: the teams table first, then the Baja app.

The table is edited on the admin Teams page (tests in test_admin_teams.py);
BVP_TEAMS_FILE is only imported into it on the first start
(test_first_start_imports.py). A car neither the table nor the Baja app
knows is unknown (404 from GET /teams/{car}, and the job's team_name stays
empty).
"""

from orchestrator.clients.teams import parse_teams_csv
from orchestrator.testing.harness import build_harness

TEAMS_CSV = """car_number,team_name,school
42,Equipe Trovão,UFSC
7,Mangue Baja,
"""


def team_answer(harness, car_number):
    response = harness.client.get(f"/teams/{car_number}", auth=harness.reviewer_auth)
    return response.status_code, response.json()


def test_the_csv_is_read_with_or_without_a_header_and_reports_bad_lines():
    # A header, a comment, a blank line, a car that is not a number and a
    # line without a team name: only the two good lines become teams, and
    # the two bad ones are reported with their line numbers.
    text = "car_number,team_name,school\n# comment\n\n42,Trovão,UFSC\nx,Bad,\n9,\n7,Mangue,\n"
    parsed = parse_teams_csv(text)
    assert sorted(parsed.teams) == [7, 42]
    assert parsed.teams[42].school == "UFSC"
    # An empty school cell becomes "no school", not the empty string.
    assert parsed.teams[7].school is None
    assert [problem.split(":")[0] for problem in parsed.problems] == ["line 5", "line 6"]
    # The header line is optional.
    assert sorted(parse_teams_csv("5,Five,\n").teams) == [5]


def test_an_excel_byte_order_mark_does_not_hide_the_header():
    # Excel saves "UTF-8 CSV" with an invisible mark in front. Without care
    # the header would look like a car called "﻿car_number".
    parsed = parse_teams_csv("﻿car_number,team_name,school\n3,Três,\n")
    assert sorted(parsed.teams) == [3]
    assert parsed.problems == []


def test_the_table_wins_over_the_baja_app(tmp_path):
    # The teams file is imported into the (empty) table on the first start.
    teams_path = tmp_path / "teams.csv"
    teams_path.write_text(TEAMS_CSV, encoding="utf-8")
    harness = build_harness(tmp_path / "data", teams_file=teams_path)
    # The app has its own (different) name for car 42 and a team for car 9.
    harness.baja_app.teams[42] = "Name from the app"
    harness.baja_app.teams[9] = "Only in the app"

    status, body = team_answer(harness, 42)
    assert (status, body["team_name"], body["school"]) == (200, "Equipe Trovão", "UFSC")
    # The app was not even asked about car 42...
    assert 42 not in harness.baja_app.requests
    # ...but it is asked about a car the table does not know.
    assert team_answer(harness, 9)[1]["team_name"] == "Only in the app"
    # Neither knows car 5: 404, i.e. "unknown".
    assert team_answer(harness, 5)[0] == 404


def test_with_the_app_down_the_table_still_answers(tmp_path):
    teams_path = tmp_path / "teams.csv"
    teams_path.write_text(TEAMS_CSV, encoding="utf-8")
    harness = build_harness(tmp_path / "data", teams_file=teams_path)
    harness.baja_app.down = True
    assert team_answer(harness, 7)[1]["team_name"] == "Mangue Baja"
    # A car only the (down) app could know is an upstream error, not "unknown".
    assert team_answer(harness, 99)[0] == 503


def test_with_an_empty_table_and_no_app_every_car_is_unknown(tmp_path):
    # No teams file and no BVP_BAJA_APP_URL: the service still starts.
    harness = build_harness(tmp_path / "data", baja_app_url="")
    assert team_answer(harness, 42)[0] == 404
    assert harness.baja_app.requests == []
