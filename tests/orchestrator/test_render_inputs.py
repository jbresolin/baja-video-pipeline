"""Clips remember what they were rendered from; edits after delivery show up (LIVESTACK-43).

The story behind these tests: João changed the cut-in of a job whose clip
was already on the studio box. Saving worked, but nothing told him the
clip on air no longer matched the form. Now:

* every clip version keeps its **render inputs**: what the rendering
  worker reported (PUT /jobs/{id}/render-inputs) or, when it reported
  nothing, the orchestrator's own snapshot of the task it handed out;
* GET /jobs/{id} answers with `clip_inputs`: the newest version's inputs
  next to what a render would use now, and the list of inputs that
  differ (`changed`). Empty right after a render; one entry per edited
  input afterwards, including a change of the name choices in Settings;
* the queue filter and "Salvar e reprocessar" are in
  test_reprocess_after_delivery.py.

Everything goes through the HTTP API, like the workers and the review UI.
"""

import hashlib

import pytest

from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole
from shared.protocol import LEASE_ID_HEADER

# Stands in for the SHA-256 of the worker's overlay theme (tokens.css).
THEME_VERSION = "7" * 64
OFFLINE_SECONDS = 60  # past the 45 s window: a worker that said nothing is offline


@pytest.fixture
def fleet(harness):
    return enroll_fleet(harness)


def enroll_fleet(harness):
    """The master (holds and renders) and the studio, plus the 26BR teams synced.

    Car 1 of the fake results API is "BAJA UFMG" (short name "Baja UFMG"),
    school "Universidade Federal de Minas Gerais - UFMG" (sigla "UFMG").
    """
    synced = harness.client.patch(
        "/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth
    )
    assert synced.status_code == 200, synced.text
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.1")
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    return master, studio


def ready_job(harness, master, **review):
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review = {"prova": "Enduro", "car_number": 1, "passagem": 3, **review}
    review_to_ready(harness, job_id, **review)
    return job_id


def report_inputs(harness, worker, job_id, lease_id, task, clip_sha256, **changes):
    """What a worker sends after rendering: the task's values as drawn, plus the theme."""
    body = {
        "clip_sha256": clip_sha256,
        "raw_sha256": task["raw_sha256"],
        "cut_in_s": task["cut_in_s"],
        "cut_out_s": task["cut_out_s"],
        "prova": task["prova"],
        "car_number": task["car_number"],
        "passagem": task["passagem"],
        "show_pass": task["show_pass"],
        "overlay_team_name": task["team_name"],
        "overlay_school_name": task["school_short"],
        "overlay_theme_version": THEME_VERSION,
        **changes,
    }
    return harness.client.put(
        f"/jobs/{job_id}/render-inputs",
        json=body,
        headers={**worker.headers, LEASE_ID_HEADER: lease_id},
    )


def render_and_push(harness, master, studio, job_id, clip=SAMPLE_CLIP, *, report=True):
    """The master claims, (reports,) and pushes to the studio, which ACKs. Returns the task."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(clip).hexdigest()
    if report:
        response = report_inputs(harness, master, job_id, lease_id, task, sha256)
        assert response.status_code == 204, response.text
    ack = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={
            "sha256": sha256,
            "size_bytes": len(clip),
            "relative_path": task["clip_relative_path"],
        },
        headers=studio.headers,
    )
    assert ack.status_code == 200, ack.text
    return task


def edit_review(harness, job_id, action="save", **fields):
    """A reviewer's PATCH, as the job page sends it."""
    job = harness.job(job_id)
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": action, **fields},
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


def choose_names(harness, **choices):
    response = harness.client.patch("/admin/settings", json=choices, auth=harness.admin_auth)
    assert response.status_code == 200, response.text


# --- Render inputs are stored per clip version ---


def test_the_workers_report_becomes_the_versions_inputs(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master, cut_in_s=3.0, cut_out_s=12.0)
    render_and_push(harness, master, studio, job_id)

    job = harness.job(job_id)
    inputs = job["clip_versions"][0]["render_inputs"]
    # Exactly what the worker said it rendered: the review fields, the
    # names as drawn (default choices: short team name, school sigla), the
    # theme version only the worker knows, and the raw.
    assert inputs == {
        "raw_sha256": job["sha256"],
        "cut_in_s": 3.0,
        "cut_out_s": 12.0,
        "prova": "Enduro",
        "car_number": 1,
        "passagem": 3,
        "show_pass": True,
        # The name choices come from the orchestrator's claim snapshot:
        # the worker only ever sees the names already chosen.
        "team_name_style": "short",
        "school_name_style": "short",
        "overlay_team_name": "Baja UFMG",
        "overlay_school_name": "UFMG",
        "overlay_theme_version": THEME_VERSION,
        # The render options (LIVESTACK-50). This report, like a 0.3.0
        # worker's, says nothing about them, which means what such a
        # worker renders: no turn, the sound kept, no framing.
        "rotation": 0,
        "framing": None,
        "crop_position": None,
        "no_audio": False,
        # No result line (LIVESTACK-47): this prova is not in the
        # pass-results configuration, and the report says none was drawn.
        "pass_result_text": None,
        "reported_by_worker": True,
    }


def test_without_a_report_the_version_keeps_the_claim_snapshot(harness, fleet):
    # An older worker renders and delivers without PUT …/render-inputs.
    master, studio = fleet
    job_id = ready_job(harness, master, cut_in_s=3.0, cut_out_s=12.0)
    render_and_push(harness, master, studio, job_id, report=False)

    inputs = harness.job(job_id)["clip_versions"][0]["render_inputs"]
    # The orchestrator knows what it handed out; only the theme is unknown.
    assert (inputs["cut_in_s"], inputs["cut_out_s"], inputs["overlay_team_name"]) == (
        3.0,
        12.0,
        "Baja UFMG",
    )
    assert inputs["overlay_theme_version"] is None
    assert inputs["reported_by_worker"] is False


def test_the_outbox_route_stores_the_inputs_too(harness, fleet):
    # Studio offline: the clip goes to the VPS outbox instead of a push.
    master, studio = fleet
    job_id = ready_job(harness, master)
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    assert report_inputs(harness, master, job_id, lease_id, task, sha256).status_code == 204
    upload = upload_in_chunks(harness, master, f"/jobs/{job_id}/clip", lease_id, SAMPLE_CLIP)
    assert upload.json()["complete"]
    version = harness.job(job_id)["clip_versions"][0]
    assert version["render_inputs"]["reported_by_worker"] is True


def test_a_report_for_another_clip_is_not_used(harness, fleet):
    # The worker reported for a render it then threw away (say the file
    # was damaged and rendered again): the delivered clip has another
    # sha256, so the report does not describe it and the snapshot is used.
    master, studio = fleet
    job_id = ready_job(harness, master)
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    other = report_inputs(harness, master, job_id, lease_id, task, "e" * 64, cut_in_s=0.5)
    assert other.status_code == 204
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={"sha256": sha256, "size_bytes": len(SAMPLE_CLIP), "relative_path": "x.mp4"},
        headers=studio.headers,
    ).raise_for_status()
    inputs = harness.job(job_id)["clip_versions"][0]["render_inputs"]
    assert inputs["reported_by_worker"] is False
    assert inputs["cut_in_s"] == 1.0  # the task's, not the stray report's 0.5


def test_a_report_needs_the_process_lease_and_the_jobs_raw(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master)
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    # The raw's hash must be this job's (a mixed-up report is refused).
    wrong_raw = report_inputs(
        harness, master, job_id, lease_id, task, "c" * 64, raw_sha256="0" * 64
    )
    assert wrong_raw.status_code == 422
    # A lease id that is not a live process lease → 409 lease_lost.
    no_lease = report_inputs(harness, master, job_id, "not-a-lease", task, "c" * 64)
    assert no_lease.status_code == 409
    assert no_lease.json()["error"]["code"] == "lease_lost"


# --- The comparison: GET /jobs/{id} → clip_inputs ---


def test_no_mismatch_right_after_a_fresh_render(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master)
    render_and_push(harness, master, studio, job_id)
    comparison = harness.job(job_id)["clip_inputs"]
    assert comparison["version"] == 1
    assert comparison["changed"] == []


def test_no_comparison_before_any_clip(harness, fleet):
    master, _ = fleet
    job_id = ready_job(harness, master)
    assert harness.job(job_id)["clip_inputs"] is None


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({"cut_in_s": 5.0}, ["cut"]),
        ({"cut_out_s": 11.0}, ["cut"]),
        ({"prova": "Suspensão"}, ["prova"]),
        # A new car number also brings that car's team and school names.
        ({"car_number": 2}, ["car_number", "team_name", "school_name"]),
        ({"passagem": 4}, ["passagem"]),
        ({"show_pass": False}, ["show_pass"]),
    ],
)
def test_each_edited_review_field_is_listed(harness, fleet, edit, expected):
    master, studio = fleet
    job_id = ready_job(harness, master, cut_in_s=3.0, cut_out_s=12.0)
    render_and_push(harness, master, studio, job_id)
    if "prova" in edit:
        harness.client.post(
            "/admin/provas", json={"name": edit["prova"]}, headers=harness.admin_headers
        ).raise_for_status()

    # The reviewer edits one field and saves (plain "Salvar").
    saved = edit_review(harness, job_id, **edit)

    # The saved job already says what no longer matches the clip on air,
    # and the clip itself is untouched (still version 1, still done).
    assert saved["clip_inputs"]["changed"] == expected
    assert saved["state"] == "done"
    assert saved["clip"]["version"] == 1


def test_the_comparison_shows_both_values(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master, cut_in_s=3.0, cut_out_s=12.0)
    render_and_push(harness, master, studio, job_id)
    comparison = edit_review(harness, job_id, cut_in_s=5.0)["clip_inputs"]
    # "O clipe no ar usa corte 0:03–0:12; a revisão agora diz 0:05–0:12."
    assert (comparison["clip"]["cut_in_s"], comparison["clip"]["cut_out_s"]) == (3.0, 12.0)
    assert (comparison["now"]["cut_in_s"], comparison["now"]["cut_out_s"]) == (5.0, 12.0)


def test_name_choices_changed_in_settings_after_delivery(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master)
    render_and_push(harness, master, studio, job_id)  # drawn as "Baja UFMG" / "UFMG"

    # The admin switches both names to "completo" on the Settings page.
    choose_names(harness, clip_team_name_style="full", clip_school_name_style="full")

    comparison = harness.job(job_id)["clip_inputs"]
    assert comparison["changed"] == ["team_name", "school_name"]
    assert comparison["clip"]["overlay_team_name"] == "Baja UFMG"
    assert comparison["now"]["overlay_team_name"] == "BAJA UFMG"
    assert comparison["now"]["overlay_school_name"] == (
        "Universidade Federal de Minas Gerais - UFMG"
    )
    assert comparison["now"]["team_name_style"] == "full"


def test_a_name_choice_that_changes_nothing_on_screen_is_not_a_mismatch(harness, fleet):
    # A team whose short name is its full name: switching the team name
    # to "completo" draws exactly the same text.
    master, studio = fleet
    harness.results_api.add_team(77, "Equipe Igual", short_name="Equipe Igual")
    harness.client.post("/admin/results-sync", auth=harness.admin_auth).raise_for_status()
    job_id = ready_job(harness, master, car_number=77)
    render_and_push(harness, master, studio, job_id)
    choose_names(harness, clip_team_name_style="full")
    # Same text either way, so a new render would look identical.
    assert harness.job(job_id)["clip_inputs"]["changed"] == []
