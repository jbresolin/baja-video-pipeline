"""The job page's "Mostrar resultado da passagem" against the REAL orchestrator (LIVESTACK-47).

The other job page tests use the fake orchestrator. This one runs the
review UI on top of the real one, so the whole path is exercised: the
admin's configuration, the results table fetched from the (fake) results
API, the text the orchestrator builds, the reviewer turning it off on the
page, and the orchestrator storing that choice.
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from tests.orchestrator.pass_results_data import job_in_review, set_up_manobrabilidade


def test_the_reviewer_sees_the_result_and_turns_it_off(reviewer, harness):
    # Setup: Manobrabilidade configured (shown by default), car 26's second
    # run in review, and its table fetched once.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)

    # The page offers the box, ticked by the prova's default, with the line.
    page = reviewer.get(f"/review/jobs/{job_id}").text
    assert 'name="show_pass_result" checked' in page
    assert "Na tarja: 66 s · 4 cones → 86 s" in page

    # What it does: the reviewer unticks it and saves (what review_form.js sends).
    version = harness.job(job_id)["version"]
    response = reviewer.patch(
        f"/review/api/jobs/{job_id}/review",
        json={"expected_version": version, "action": "save", "show_pass_result": False},
    )
    assert response.status_code == 200, response.text

    # The orchestrator stored the choice, and the reloaded page shows it:
    # unticked, with the result it would have burned in.
    assert harness.job(job_id)["show_pass_result"] is False
    page = reviewer.get(f"/review/jobs/{job_id}").text
    assert 'name="show_pass_result" >' in page
    assert "Não será gravado (resultado: 66 s · 4 cones → 86 s)." in page


def test_a_save_without_the_field_keeps_the_choice(reviewer, harness):
    # A save from a page without the box (or an older page) leaves it alone.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    version = harness.job(job_id)["version"]
    reviewer.patch(
        f"/review/api/jobs/{job_id}/review",
        json={"expected_version": version, "action": "save", "show_pass_result": False},
    )

    response = reviewer.patch(
        f"/review/api/jobs/{job_id}/review",
        json={"expected_version": version + 1, "action": "save", "passagem": 1},
    )

    assert response.status_code == 200, response.text
    assert harness.job(job_id)["show_pass_result"] is False
