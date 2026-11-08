"""The result line and the clock for values not saved yet (LIVESTACK-53).

The story: on a new video the reviewer chooses the prova and types car
and passagem. The job page used to show "Resultado da passagem" and
"Cronômetro" only after a save and a reload, because it built them from
the saved job, so the largada could not be marked before saving. Now the
page asks GET /jobs/{id}/pass-preview with what the form says, and the
orchestrator answers what Job.pass_result and Job.pass_clock *would be*
after saving those values, without saving anything.

Everything goes through the HTTP API of a real orchestrator (the test
harness), with the real results of the 2026 Manobrabilidade: car 26's
second run took 66 s with 4 cones (86 s corrected), its first 59 s clean.
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job, save_review_fields
from shared.models import WorkerRole
from tests.orchestrator.pass_results_data import job_in_review, set_up_manobrabilidade


def ask_preview(harness, job_id: str, **values):
    """GET /jobs/{id}/pass-preview as the reviewer, with the given form values."""
    return harness.client.get(
        f"/jobs/{job_id}/pass-preview", params=values, auth=harness.reviewer_auth
    )


def new_video(harness) -> str:
    """A video in the review queue with nothing typed yet: no prova, car or passagem."""
    worker = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    return job_id


def fetch_the_manobrabilidade_table(harness) -> None:
    """Have the background loop fetch 26BR_MAN once.

    It only fetches tables of provas that have jobs, so another
    Manobrabilidade video (as on a real event day) is put in review first.
    """
    job_in_review(harness, car=2, passagem=1)
    poll_due_tables(harness.context)


def test_a_new_video_gets_the_result_and_the_clock_for_what_the_form_says(harness):
    # Setup: Manobrabilidade has a result line and a clock; its table is
    # fetched. A new video has nothing saved: the job itself shows neither.
    set_up_manobrabilidade(harness)
    fetch_the_manobrabilidade_table(harness)
    job_id = new_video(harness)
    assert harness.job(job_id)["pass_clock"]["configured"] is False

    # The form says Manobrabilidade, car 26, passagem 2 (nothing saved).
    response = ask_preview(harness, job_id, prova="Manobrabilidade", car_number=26, passagem=2)

    # The answer is the result line and the clock for those values: the
    # clock stops on 66,0 and waits for a largada.
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["pass_result"]["configured"] is True
    assert preview["pass_result"]["text"] == "66 s · 4 cones → 86 s"
    clock = preview["pass_clock"]
    assert (clock["configured"], clock["show"], clock["official_text"]) == (True, True, "66,0")
    assert clock["render"] is None


def test_nothing_is_saved_by_asking(harness):
    set_up_manobrabilidade(harness)
    fetch_the_manobrabilidade_table(harness)
    job_id = new_video(harness)
    before = harness.job(job_id)

    ask_preview(harness, job_id, prova="Manobrabilidade", car_number=26, passagem=2)

    # Same version, same fields: asking changed nothing on the job.
    after = harness.job(job_id)
    assert after["version"] == before["version"]
    assert (after["prova"], after["car_number"], after["passagem"]) == (None, None, None)


def test_the_answer_is_exactly_what_the_job_shows_after_saving_the_same_values(harness):
    # The promise of the endpoint: what the page shows while typing is what
    # it will show after the save. Asked first, then saved, then compared.
    set_up_manobrabilidade(harness)
    fetch_the_manobrabilidade_table(harness)
    job_id = new_video(harness)
    values = {"prova": "Manobrabilidade", "car_number": 26, "passagem": 2}
    preview = ask_preview(harness, job_id, **values).json()

    save_review_fields(harness, job_id, **values)

    job = harness.job(job_id)
    assert preview["pass_result"] == job["pass_result"]
    assert preview["pass_clock"] == job["pass_clock"]


def test_a_prova_without_a_clock_answers_not_configured(harness):
    # Enduro is a prova of the test orchestrator with no pass-results
    # configuration: no result line, no clock. The page removes both blocks.
    set_up_manobrabilidade(harness)
    job_id = new_video(harness)

    preview = ask_preview(harness, job_id, prova="Enduro", car_number=26, passagem=2).json()

    assert preview["pass_result"]["configured"] is False
    assert preview["pass_clock"]["configured"] is False


def test_switching_the_prova_back_and_forth_adds_and_removes_the_clock(harness):
    # A saved Manobrabilidade job: asking for Enduro takes the clock away,
    # asking for Manobrabilidade again brings it back.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)

    enduro = ask_preview(harness, job_id, prova="Enduro").json()
    back = ask_preview(harness, job_id, prova="Manobrabilidade").json()

    assert enduro["pass_clock"]["configured"] is False
    assert back["pass_clock"]["configured"] is True


def test_a_value_left_out_keeps_the_saved_one(harness):
    # Saved: Manobrabilidade, car 26, passagem 2. Only the passagem is
    # asked (1): prova and car come from the job, as a save that leaves
    # them out keeps them. Car 26's first run: 59 s, no penalties.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)

    preview = ask_preview(harness, job_id, passagem=1).json()

    assert preview["pass_result"]["text"] == "59 s"
    assert preview["pass_clock"]["official_text"] == "59,0"


def test_the_saved_largada_and_toggle_are_used(harness):
    # The largada is saved on the job; the preview for another passagem
    # keeps it, so `render` says what a render would draw after the save.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)
    save_review_fields(harness, job_id, largada_s=1.5)

    clock = ask_preview(harness, job_id, passagem=1).json()["pass_clock"]

    assert clock["render"]["largada_s"] == 1.5
    assert clock["render"]["official_units"] == 590


def test_bad_values_and_unknown_jobs_are_refused(harness):
    set_up_manobrabilidade(harness)
    job_id = new_video(harness)

    # A passagem of 0 and a car of 10000 are outside what the form allows.
    assert ask_preview(harness, job_id, passagem=0).status_code == 400
    assert ask_preview(harness, job_id, car_number=10000).status_code == 400
    assert ask_preview(harness, "no-such-job", passagem=1).status_code == 404


def test_only_reviewers_may_ask(harness):
    # A worker reads jobs, but has no business with a reviewer's typing.
    set_up_manobrabilidade(harness)
    job_id = new_video(harness)
    worker = harness.enroll(WorkerRole.MASTER, "other-master")

    response = harness.client.get(
        f"/jobs/{job_id}/pass-preview", params={"passagem": 1}, headers=worker.headers
    )

    assert response.status_code in (401, 403)
