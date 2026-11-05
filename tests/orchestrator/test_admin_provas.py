"""The provas list (admin Provas page) and what it means for reviews and clip folders.

Before: `prova` was free text. Now there is a list. A reviewer picks from
it, and a job is only made ready with a prova that is in the list
(422 `unknown_prova` otherwise).

Each prova has a "slug": the folder name its clips go into on the studio
box ("Suspensão" → "suspensao"). The rule chosen for renames: the slug is
fixed when the prova is created. Renaming changes the name (and relabels
every job that had the old name) but keeps the slug, so clips keep going
into the same folder and OBS keeps finding them.
"""

from orchestrator.testing.harness import EnrolledWorker
from orchestrator.testing.scenarios import (
    ask_next,
    claim_ok,
    ensure_prova,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def provas(harness) -> list[dict]:
    return harness.client.get("/provas", auth=harness.reviewer_auth).json()["provas"]


def add_prova(harness, name):
    return harness.client.post("/admin/provas", json={"name": name}, auth=harness.admin_auth)


def change_prova(harness, prova_id, **fields):
    return harness.client.patch(f"/admin/provas/{prova_id}", json=fields, auth=harness.admin_auth)


def job_in_review(harness) -> tuple[EnrolledWorker, str]:
    """A job waiting for review, held by a master that rendered its proxy."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    return master, job_id


def test_a_new_prova_gets_a_slug_and_goes_to_the_end_of_the_list(harness):
    first = add_prova(harness, "Enduro").json()
    second = add_prova(harness, "  Suspensão   e  Tração ").json()
    # Extra spaces are tidied; the slug is ASCII lowercase with dashes.
    assert (second["name"], second["slug"]) == ("Suspensão e Tração", "suspensao-e-tracao")
    assert [p["display_order"] for p in (first, second)] == [1, 2]
    # Reviewers can read the list (the review form's select).
    assert [p["name"] for p in provas(harness)] == ["Enduro", "Suspensão e Tração"]


def test_a_name_or_clip_folder_that_is_taken_is_refused(harness):
    add_prova(harness, "Enduro")
    same_name = add_prova(harness, "Enduro")
    # "ENDURO" is another name but the same folder: two provas would mix
    # their clips, so it is refused too.
    same_folder = add_prova(harness, "ENDURO")
    for response in (same_name, same_folder):
        assert (response.status_code, response.json()["error"]["code"]) == (409, "already_exists")
    # A name with no letters or digits cannot become a folder.
    assert add_prova(harness, "!!!").status_code == 400


def test_renaming_keeps_the_slug_and_relabels_existing_jobs(harness):
    _, job_id = job_in_review(harness)
    review_to_ready(harness, job_id, prova="Enduro")
    enduro = next(p for p in provas(harness) if p["name"] == "Enduro")

    renamed = change_prova(harness, enduro["id"], name="Enduro de Resistência").json()

    # Same folder as before the rename...
    assert renamed["slug"] == "enduro"
    # ...and the job now carries the new name, so it is still valid.
    assert harness.job(job_id)["prova"] == "Enduro de Resistência"


def test_the_clip_path_uses_the_stored_slug_not_the_new_name(harness):
    master, job_id = job_in_review(harness)
    review_to_ready(harness, job_id, prova="Enduro", car_number=42, passagem=3)
    enduro = next(p for p in provas(harness) if p["name"] == "Enduro")
    change_prova(harness, enduro["id"], name="Resistência")
    # The render offer tells the worker where the clip goes on the studio box.
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    # slugify("Resistência") would be "resistencia"; the stored slug wins.
    assert offer["clip_relative_path"] == "enduro/42/3.mp4"


def test_ready_needs_a_prova_from_the_list(harness):
    _, job_id = job_in_review(harness)
    job = harness.job(job_id)
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={
            "expected_version": job["version"],
            "action": "ready",
            "prova": "Prova Inventada",
            "car_number": 1,
            "passagem": 1,
            "cut_in_s": 1,
            "cut_out_s": 2,
        },
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_prova"
    # "save" is work in progress and is not checked.
    saved = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "save", "prova": "Rascunho"},
        auth=harness.reviewer_auth,
    )
    assert saved.status_code == 200


def test_a_deactivated_prova_stays_valid_for_jobs_that_have_it(harness):
    master, job_id = job_in_review(harness)
    review_to_ready(harness, job_id, prova="Enduro")
    enduro = next(p for p in provas(harness) if p["name"] == "Enduro")
    assert change_prova(harness, enduro["id"], active=False).json()["active"] is False
    # Still listed (flagged inactive), so the review UI can show it on old jobs.
    assert [(p["name"], p["active"]) for p in provas(harness)] == [("Enduro", False)]
    # A reprocess of the old job keeps working without picking another prova.
    claim_ok(harness, master, job_id, TaskKind.PROCESS)
    job = harness.job(job_id)
    reprocess = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "reprocess", "cut_in_s": 2},
        auth=harness.reviewer_auth,
    )
    assert reprocess.status_code == 200, reprocess.text


def test_reorder_needs_every_prova_exactly_once(harness):
    ids = [add_prova(harness, name).json()["id"] for name in ("A1", "B2", "C3")]
    reordered = harness.client.put(
        "/admin/provas/order", json={"prova_ids": [ids[2], ids[0], ids[1]]}, auth=harness.admin_auth
    )
    assert [p["name"] for p in reordered.json()["provas"]] == ["C3", "A1", "B2"]
    assert [p["name"] for p in provas(harness)] == ["C3", "A1", "B2"]
    # Leaving one out (or repeating one) is refused, so none gets lost.
    partial = harness.client.put(
        "/admin/provas/order", json={"prova_ids": [ids[0], ids[0], ids[1]]}, auth=harness.admin_auth
    )
    assert partial.status_code == 400


def test_prova_changes_are_audited(harness):
    prova_id = add_prova(harness, "Enduro").json()["id"]
    change_prova(harness, prova_id, name="Enduro 4h")
    events = harness.client.get("/admin/audit?area=provas", auth=harness.admin_auth).json()[
        "events"
    ]
    assert [event["kind"] for event in events] == ["prova_updated", "prova_created"]
    assert events[0]["details"]["before"]["name"] == "Enduro"
    assert events[0]["details"]["after"]["name"] == "Enduro 4h"


def test_ensure_prova_helper_is_idempotent(harness):
    # A sanity check of the test helper other tests rely on.
    assert ensure_prova(harness, "Enduro")["id"] == ensure_prova(harness, "Enduro")["id"]
