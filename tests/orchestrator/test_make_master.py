""" "Tornar master" on Admin → Workers (LIVESTACK-42 item 8).

Background for a beginner: the fleet has exactly one *master*, the PC that
keeps a copy of every raw and archives every finished clip (design §4,
§11). Until now the master was fixed at enrollment and changing it meant
editing worker.json by hand. Now an admin presses "Tornar master" on a
worker: in one step that worker becomes the master and the current master
becomes a plain worker.

The PCs learn it from their next heartbeat: a worker sends the role it is
running as (`role` in the heartbeat) and the answer carries the role the
orchestrator has for it. Workers older than this change send no `role` and
get none back, because their parser refuses unknown fields.
"""

import hashlib

import pytest

from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


@pytest.fixture
def fleet(harness):
    """The master, two volunteers and the studio, all heartbeating."""
    master = harness.enroll(WorkerRole.MASTER, "joao-desktop", tailnet_address="100.70.0.1")
    maria = harness.enroll(WorkerRole.WORKER, "pc-da-maria", tailnet_address="100.70.0.2")
    pedro = harness.enroll(WorkerRole.WORKER, "pc-do-pedro", tailnet_address="100.70.0.3")
    studio = harness.enroll(WorkerRole.STUDIO, "estudio", tailnet_address="100.70.0.4")
    return master, maria, pedro, studio


def make_master(harness, worker_id: str):
    return harness.client.post(f"/admin/workers/{worker_id}/make-master", auth=harness.admin_auth)


def roles(harness) -> dict[str, str]:
    """Display name → role, as the fleet shows it."""
    fleet = harness.client.get("/fleet", auth=harness.admin_auth).json()["workers"]
    return {entry["worker"]["name"]: entry["worker"]["role"] for entry in fleet}


# --- Promoting ---


def test_promoting_a_worker_demotes_the_master_in_one_step(harness, fleet):
    _, maria, _, _ = fleet

    response = make_master(harness, maria.worker_id)

    # The answer is the promoted worker; the fleet shows exactly one master.
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "master"
    assert roles(harness) == {
        "joao-desktop": "worker",
        "pc-da-maria": "master",
        "pc-do-pedro": "worker",
        "estudio": "studio",
    }


def test_the_change_is_audited_naming_old_and_new_master(harness, fleet):
    master, maria, _, _ = fleet

    make_master(harness, maria.worker_id)

    audit = harness.client.get("/admin/audit?area=workers", auth=harness.admin_auth).json()
    [event] = audit["events"]
    assert event["kind"] == "master_changed"
    assert event["details"]["before"] == {
        "masters": [{"worker_id": master.worker_id, "name": "joao-desktop"}]
    }
    assert event["details"]["after"] == {
        "masters": [{"worker_id": maria.worker_id, "name": "pc-da-maria"}]
    }
    assert "'pc-da-maria' is now the master; 'joao-desktop' is a worker again" in event["reason"]


def test_promoting_with_no_master_just_promotes(harness):
    maria = harness.enroll(WorkerRole.WORKER, "pc-da-maria")

    assert make_master(harness, maria.worker_id).status_code == 200

    assert roles(harness) == {"pc-da-maria": "master"}


def test_promoting_the_master_again_changes_nothing(harness, fleet):
    master, *_ = fleet

    response = make_master(harness, master.worker_id)

    assert response.status_code == 200
    assert response.json()["role"] == "master"
    audit = harness.client.get("/admin/audit?area=workers", auth=harness.admin_auth).json()
    assert audit["events"] == []


# --- What is refused ---


def test_a_studio_cannot_be_promoted(harness, fleet):
    # The studio has another tailnet tag (tag:studio); it must enroll again.
    *_, studio = fleet

    response = make_master(harness, studio.worker_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "enroll that PC again with a master token" in response.json()["error"]["message"]
    assert roles(harness)["joao-desktop"] == "master"


def test_a_revoked_worker_cannot_be_promoted_and_unknown_ids_are_404(harness, fleet):
    _, maria, _, _ = fleet
    harness.client.post(f"/admin/workers/{maria.worker_id}/revoke", auth=harness.admin_auth)

    assert make_master(harness, maria.worker_id).status_code == 409
    assert make_master(harness, "0" * 32).status_code == 404


def test_only_an_admin_may_promote(harness, fleet):
    _, maria, _, _ = fleet

    as_reviewer = harness.client.post(
        f"/admin/workers/{maria.worker_id}/make-master", auth=harness.reviewer_auth
    )

    assert as_reviewer.status_code == 403


# --- How the PCs learn it: the heartbeat answer ---


def test_the_heartbeat_answer_carries_the_new_role_to_both_pcs(harness, fleet):
    master, maria, _, _ = fleet
    # Before: each PC says what it runs as and hears the same back.
    assert harness.heartbeat(master, role="master")["role"] == "master"
    assert harness.heartbeat(maria, role="worker")["role"] == "worker"

    make_master(harness, maria.worker_id)

    # After: both still run in their old role until they restart, and the
    # answer tells each one its new role (worker.role_change acts on it).
    assert harness.heartbeat(master, role="master")["role"] == "worker"
    assert harness.heartbeat(maria, role="worker")["role"] == "master"


def test_an_older_worker_that_sends_no_role_gets_no_role_field(harness, fleet):
    # Workers before this change refuse unknown fields in the answer, so the
    # key must be absent from the JSON, not merely null.
    master, *_ = fleet

    answer = harness.heartbeat(master)

    assert "role" not in answer


# --- What changes in the work they are offered ---


def test_backfill_and_archive_follow_the_new_master(harness, fleet):
    master, maria, _, studio = fleet
    # Job 1: held by the old master and Maria; Maria renders it and pushes
    # it to the studio, then keeps the clip.
    rendered = receive_job(harness)
    hold_raw(harness, master, rendered)
    hold_raw(harness, maria, rendered)
    make_proxy(harness, master, rendered)
    review_to_ready(harness, rendered, prova="Enduro", car_number=42, passagem=1)
    claim_ok(harness, maria, rendered, TaskKind.PROCESS)
    clip_sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    delivered = harness.client.post(
        f"/jobs/{rendered}/delivered",
        json={"sha256": clip_sha256, "size_bytes": len(SAMPLE_CLIP), "relative_path": "x/42/1.mp4"},
        headers=studio.headers,
    )
    assert delivered.status_code == 200, delivered.text
    harness.heartbeat(
        maria, held_job_ids=[rendered], held_clips=[{"job_id": rendered, "sha256": clip_sha256}]
    )
    # The old master starts archiving that clip (it holds a lease on it).
    claim_ok(harness, master, rendered, TaskKind.ARCHIVE_CLIP)
    # Job 2: only the old master holds its raw so far.
    only_old_master = receive_job(harness)
    hold_raw(harness, master, only_old_master)

    make_master(harness, maria.worker_id)

    # The old master's archive lease ended at once ("no longer the master")
    # and it is offered no archive work any more.
    leases = harness.job(rendered)["leases"]
    assert all(lease["task_kind"] != "archive_clip" for lease in leases)
    assert ask_next(harness, master, [TaskKind.ARCHIVE_CLIP]) is None
    # Maria, the new master, must end up with every raw and every clip: she
    # is offered the raw she lacks, and the archive copy of her own clip.
    assert ask_next(harness, maria, [TaskKind.DOWNLOAD_RAW])["job_id"] == only_old_master
    assert ask_next(harness, maria, [TaskKind.ARCHIVE_CLIP])["job_id"] == rendered
