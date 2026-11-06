"""Full or short names on the clips, chosen on the Settings page (LIVESTACK-42 item 6).

Background for a beginner: every team has a full name ("Equipe FEI Baja")
and usually a short one ("FEI"); every school a full name ("Fundação
Educacional Inaciana - FEI") and a sigla ("FEI"). The Settings page has two
independent choices:

    Nome da equipe nos vídeos:  completo / curto   (setting clip_team_name_style)
    Nome da escola nos vídeos:  completo / sigla   (setting clip_school_name_style)

The defaults (curto, sigla) are what clips showed before. A missing short
name or sigla falls back to the full one, and the other way round. The
choice reaches:

* the render task the worker draws the overlay from (team_name and
  school_short of the `process` offer);
* the clip manifest (the review UI's clips page) and the team lookup (the
  job page), as `clip_team_name` / `clip_school_name`.

The four-combinations test below ends in tests/worker/overlay_golden.py:
what the worker draws for each choice is exactly the text of an approved
golden image.
"""

import pytest

from orchestrator.domain.clip_names import NameStyles, clip_names
from orchestrator.testing.harness import EnrolledWorker
from orchestrator.testing.scenarios import (
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import NameStyle, ProcessTask, TaskKind, WorkerRole
from tests.worker.overlay_golden import GOLDEN_CASES, golden_case_id
from worker.tasks.process import overlay_text_for


def choose(harness, team: str | None = None, school: str | None = None) -> dict:
    """Save the two choices the way the Settings page does."""
    changes = {}
    if team is not None:
        changes["clip_team_name_style"] = team
    if school is not None:
        changes["clip_school_name_style"] = school
    response = harness.client.patch("/admin/settings", json=changes, auth=harness.admin_auth)
    assert response.status_code == 200, response.text
    return response.json()["settings"]


def reviewed_job_offer(harness, car_number: int) -> tuple[EnrolledWorker, dict]:
    """Sync the 26BR event, review a video of `car_number`; (the master, its render offer)."""
    choose_event = harness.client.patch(
        "/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth
    )
    assert choose_event.status_code == 200
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro", car_number=car_number, passagem=3)
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer is not None
    return master, offer


# --- The rule itself ---


def test_each_choice_picks_its_name_and_falls_back_to_the_other():
    full = NameStyles(team=NameStyle.FULL, school=NameStyle.FULL)
    short = NameStyles()  # the defaults: short name and sigla

    def names(styles, **values):
        picked = clip_names(styles, **values)
        return picked.team, picked.school

    every_name = {
        "team_full": "Equipe FEI Baja",
        "team_short": "FEI",
        "school_full": "Fundação Educacional Inaciana - FEI",
        "school_short": "FEI",
    }
    assert names(full, **every_name) == ("Equipe FEI Baja", "Fundação Educacional Inaciana - FEI")
    assert names(short, **every_name) == ("FEI", "FEI")
    # No short name and no sigla: "curto"/"sigla" show the full names.
    only_full = {"team_full": "Equipe X", "team_short": None, "school_full": "Escola X"}
    assert names(short, **only_full, school_short="  ") == ("Equipe X", "Escola X")
    # No full school name (a job reviewed before this change): "completo"
    # shows the sigla rather than nothing.
    assert names(
        full, team_full="Equipe X", team_short="X", school_full=None, school_short="UX"
    ) == (
        "Equipe X",
        "UX",
    )
    # Nothing known at all stays empty: the overlay then leaves the part out.
    assert names(full, team_full=None, team_short=None, school_full=None, school_short=None) == (
        None,
        None,
    )


# --- Settings ---


def test_the_defaults_are_short_name_and_sigla(harness):
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()["settings"]

    assert settings["clip_team_name_style"] == "short"
    assert settings["clip_school_name_style"] == "short"


def test_the_two_choices_are_independent_and_audited(harness):
    saved = choose(harness, team="full")

    # Only the team changed; the school keeps its sigla.
    assert (saved["clip_team_name_style"], saved["clip_school_name_style"]) == ("full", "short")
    audit = harness.client.get("/admin/audit?area=settings", auth=harness.admin_auth).json()
    assert audit["events"][0]["details"]["after"] == {"clip_team_name_style": "full"}


def test_an_unknown_choice_is_refused(harness):
    response = harness.client.patch(
        "/admin/settings", json={"clip_school_name_style": "abbreviated"}, auth=harness.admin_auth
    )

    assert response.status_code == 400


# --- What the worker draws: the four combinations, against the golden images ---


@pytest.mark.parametrize("choice", sorted(GOLDEN_CASES), ids=golden_case_id)
def test_each_choice_renders_exactly_its_golden_image_text(harness, choice):
    # Car 2 of the fake results API: "Equipe FEI Baja" / "FEI", school
    # "Fundação Educacional Inaciana - FEI" / "FEI".
    team_style, school_style = choice
    choose(harness, team=team_style, school=school_style)

    _, offer = reviewed_job_offer(harness, car_number=2)

    # The worker turns the offer into overlay text; it is exactly the text
    # of the approved golden image for this choice (tests/worker/golden/).
    expected_text, _ = GOLDEN_CASES[choice]
    assert overlay_text_for(ProcessTask.model_validate(offer)) == expected_text


def test_a_change_applies_to_renders_offered_after_it(harness):
    # Reviewed under the defaults: the offer carries the short names.
    master, before = reviewed_job_offer(harness, car_number=2)
    assert (before["team_name"], before["school_short"]) == ("FEI", "FEI")

    # The admin picks the full names before anyone claimed the render.
    choose(harness, team="full", school="full")

    # An offer is not a reservation, so asking again offers the same job,
    # now with the full names: the choice is read when the task is offered.
    after = ask_next(harness, master, [TaskKind.PROCESS])
    assert after["job_id"] == before["job_id"]
    assert (after["team_name"], after["school_short"]) == (
        "Equipe FEI Baja",
        "Fundação Educacional Inaciana - FEI",
    )


# --- What the review UI is told ---


def test_the_team_lookup_says_how_a_clip_names_the_team_and_school(harness):
    choose(harness, team="full", school="short")
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)

    team = harness.client.get("/teams/2", auth=harness.reviewer_auth).json()

    assert (team["clip_team_name"], team["clip_school_name"]) == ("Equipe FEI Baja", "FEI")
    # The stored names themselves are unchanged.
    assert (team["team_name"], team["short_name"]) == ("Equipe FEI Baja", "FEI")


def test_the_clip_manifest_names_team_and_school_by_the_choice(harness):
    # A clip of car 2, rendered and pushed straight to the studio.
    master, offer = reviewed_job_offer(harness, car_number=2)
    studio = harness.enroll(WorkerRole.STUDIO, "estudio")
    claim_ok(harness, master, offer["job_id"], TaskKind.PROCESS)
    delivered = harness.client.post(
        f"/jobs/{offer['job_id']}/delivered",
        json={"sha256": "c" * 64, "size_bytes": 10, "relative_path": offer["clip_relative_path"]},
        headers=studio.headers,
    )
    assert delivered.status_code == 200, delivered.text

    def manifest_names() -> tuple[str, str, str]:
        [clip] = harness.client.get("/manifest", auth=harness.reviewer_auth).json()["clips"]
        return clip["team_name"], clip["clip_team_name"], clip["clip_school_name"]

    # Under the defaults the clips page shows the short names...
    assert manifest_names() == ("Equipe FEI Baja", "FEI", "FEI")
    # ...and follows the choice; team_name itself stays the full name.
    choose(harness, team="full", school="full")
    assert manifest_names() == (
        "Equipe FEI Baja",
        "Equipe FEI Baja",
        "Fundação Educacional Inaciana - FEI",
    )
