"""Settings: "Esperar o resultado oficial por até N minutos" (LIVESTACK-62).

What this module is for: how long "Pronto" with "Esperar o resultado
oficial" ticked may hold a render is a value on the admin Settings page.

What these tests prove: it starts at 15 minutes and the job page is told
it (for the checkbox's label); whole minutes from 1 to 120 are accepted and
anything else is refused; a change applies to holds started afterwards,
never to a hold already running.

What they do not cover: the Settings page itself (the review UI tests).
"""

from datetime import timedelta

import pytest

from tests.orchestrator.result_hold_helpers import (
    DEFAULT_WAIT,
    as_time,
    click_pronto,
    job_waiting_for_review,
    manobrabilidade_and_master,
)


def settings_of(harness) -> dict:
    """What the admin Settings page reads (GET /admin/settings)."""
    response = harness.client.get("/admin/settings", auth=harness.admin_auth)
    response.raise_for_status()
    return response.json()["settings"]


def change_wait(harness, minutes):
    """The admin saves a new number of minutes on the Settings page."""
    return harness.client.patch(
        "/admin/settings", json={"result_wait_minutes": minutes}, auth=harness.admin_auth
    )


def test_the_wait_starts_at_15_minutes_and_the_job_page_is_told(harness):
    # Arrange: a fresh orchestrator and one job in review.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master)

    # Act: read the Settings and the job.
    stored = settings_of(harness)["result_wait_minutes"]
    on_the_job = harness.job(job_id)["result_wait_minutes"]

    # Assert: 15 everywhere; the job page writes "(até 15 min)" next to "Pronto".
    assert stored == 15, "the default wait is 15 minutes"
    assert on_the_job == 15, "the job carries the value for the checkbox's label"


@pytest.mark.parametrize(
    "minutes",
    [
        1,  # the shortest wait allowed
        120,  # the longest: two hours
    ],
)
def test_whole_minutes_from_1_to_120_are_accepted(harness, minutes):
    # Arrange: nothing beyond a fresh orchestrator.

    # Act: the admin saves the value.
    response = change_wait(harness, minutes)

    # Assert: stored as typed.
    assert response.status_code == 200, response.text
    assert settings_of(harness)["result_wait_minutes"] == minutes, "the value must be stored"


@pytest.mark.parametrize(
    "minutes",
    [
        0,  # no wait at all: that is what unticking the box is for
        121,  # more than two hours would keep a clip off air too long
        -5,  # a typing mistake
        7.5,  # minutes are whole
    ],
)
def test_values_outside_1_to_120_whole_minutes_are_refused(harness, minutes):
    # Arrange: nothing beyond a fresh orchestrator.

    # Act: the admin tries to save the value.
    response = change_wait(harness, minutes)

    # Assert: refused, and the stored value stays 15.
    assert response.status_code == 400, f"{minutes} must be refused"
    assert settings_of(harness)["result_wait_minutes"] == 15, "the old value stays"


def test_a_change_applies_to_new_holds_only(harness):
    # Arrange: car 5's second run held for the default 15 minutes.
    master = manobrabilidade_and_master(harness)
    first_job = job_waiting_for_review(harness, master, car=5, passagem=2)
    first = click_pronto(harness, first_job)

    # Act: the admin shortens the wait to 5 minutes; another video is made ready.
    assert change_wait(harness, 5).status_code == 200
    second_job = job_waiting_for_review(harness, master, car=5, passagem=2)
    second = click_pronto(harness, second_job)

    # Assert: the running hold keeps its 15 minutes; the new one gets 5.
    held_at = as_time(first["reviewed_at"])
    assert as_time(harness.job(first_job)["result_hold_until"]) == held_at + DEFAULT_WAIT, (
        "a hold already running keeps its deadline"
    )
    assert as_time(second["result_hold_until"]) == harness.clock.now() + timedelta(minutes=5), (
        "a new hold uses the new value"
    )
