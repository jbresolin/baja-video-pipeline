"""Tests for the team lookup (GET /teams/{car}): the teams table and nothing else.

The table is filled by the results API sync (test_results_sync.py) and
corrected on the admin Teams page (test_admin_teams.py); an old
BVP_TEAMS_FILE is imported into it on the first start
(test_first_start_imports.py). A car the table does not know is unknown
(404, and the job's team_name stays empty).
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


def test_teams_from_the_old_csv_answer_lookups(tmp_path):
    # A VPS that had BVP_TEAMS_FILE: imported once into the empty table.
    teams_path = tmp_path / "teams.csv"
    teams_path.write_text(TEAMS_CSV, encoding="utf-8")
    harness = build_harness(tmp_path / "data", teams_file=teams_path)

    status, body = team_answer(harness, 42)
    assert (status, body["team_name"], body["school"]) == (200, "Equipe Trovão", "UFSC")
    # A car the table does not know: 404, i.e. "unknown". Nothing else is
    # asked (the old Baja app lookup is gone; the results API is synced,
    # not asked per car).
    assert team_answer(harness, 5)[0] == 404
    assert harness.results_api.requests == []


def test_with_an_empty_table_every_car_is_unknown(tmp_path):
    harness = build_harness(tmp_path / "data")
    assert team_answer(harness, 42)[0] == 404
