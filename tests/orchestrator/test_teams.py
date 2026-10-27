"""Tests for the team lookup adapter (design §17, "Team lookup").

Two optional sources: the BVP_TEAMS_FILE CSV first, then the Baja app
(BVP_BAJA_APP_URL); a car neither knows is unknown (404 from
GET /teams/{car}, and the job's team_name stays empty).
"""

import os

from orchestrator.clients.teams import TeamsFile, parse_teams_csv
from orchestrator.testing.harness import build_harness

TEAMS_CSV = """car_number,team_name,school
42,Equipe Trovão,UFSC
7,Mangue Baja,
"""


def team_answer(harness, car_number):
    response = harness.client.get(f"/teams/{car_number}", auth=harness.reviewer_auth)
    return response.status_code, response.json()


def test_the_csv_is_read_with_or_without_a_header_and_skips_bad_lines():
    # A header, a comment, a blank line, a car that is not a number and a
    # line without a team name: only the two good lines become teams.
    text = "car_number,team_name,school\n# comment\n\n42,Trovão,UFSC\nx,Bad,\n9,\n7,Mangue,\n"
    teams = parse_teams_csv(text)
    assert sorted(teams) == [7, 42]
    assert teams[42].school == "UFSC"
    # An empty school cell becomes "no school", not the empty string.
    assert teams[7].school is None
    # The header line is optional.
    assert sorted(parse_teams_csv("5,Five,\n")) == [5]


def test_the_file_is_reloaded_when_it_changes(tmp_path):
    path = tmp_path / "teams.csv"
    path.write_text("1,Old name,\n", encoding="utf-8")
    teams_file = TeamsFile(path)
    assert teams_file.find_team(1).team_name == "Old name"
    # Edit the file. The modification time is pushed forward explicitly,
    # because two writes within the same instant could look unchanged.
    path.write_text("1,New name,\n2,Second,\n", encoding="utf-8")
    later = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(later, later))
    assert teams_file.find_team(1).team_name == "New name"
    assert teams_file.find_team(2).team_name == "Second"


def test_a_missing_file_simply_knows_no_teams(tmp_path):
    assert TeamsFile(tmp_path / "nope.csv").find_team(1) is None


def test_the_file_wins_over_the_baja_app(tmp_path):
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
    # ...but it is asked about a car the file does not know.
    assert team_answer(harness, 9)[1]["team_name"] == "Only in the app"
    # Neither knows car 5: 404, i.e. "unknown".
    assert team_answer(harness, 5)[0] == 404


def test_with_the_app_down_the_file_still_answers(tmp_path):
    teams_path = tmp_path / "teams.csv"
    teams_path.write_text(TEAMS_CSV, encoding="utf-8")
    harness = build_harness(tmp_path / "data", teams_file=teams_path)
    harness.baja_app.down = True
    assert team_answer(harness, 7)[1]["team_name"] == "Mangue Baja"
    # A car only the (down) app could know is an upstream error, not "unknown".
    assert team_answer(harness, 99)[0] == 503


def test_with_no_source_configured_every_car_is_unknown(tmp_path):
    # Neither BVP_TEAMS_FILE nor BVP_BAJA_APP_URL: the service still starts.
    harness = build_harness(tmp_path / "data", baja_app_url="")
    assert team_answer(harness, 42)[0] == 404
    assert harness.baja_app.requests == []
