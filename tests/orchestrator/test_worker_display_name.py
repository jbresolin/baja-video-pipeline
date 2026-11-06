"""A worker's display name, editable by an admin (LIVESTACK-42 item 7).

Background for a beginner: every PC that joins the fleet ("worker") has a
host name, the name Windows gave it ("DESKTOP-8F3K2LQ"). That often means
nothing to the people running the event. So a worker also has a display
name: the volunteer types it on the token form (prefilled with the host
name) and it reaches the orchestrator as `worker_name` at enrollment; an
admin can change it later with PATCH /admin/workers/{id}. Every page shows
the display name (`Worker.name`); the host name stays visible as
`Worker.hostname`.
"""

from shared.models import WorkerRole


def enroll_with_hostname(harness, display_name: str, hostname: str) -> str:
    """Issue a token and enroll a PC the way the worker app does; return its id."""
    created = harness.client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": "Maria", "role": "worker"},
        headers=harness.admin_headers,
    )
    enrolled = harness.client.post(
        "/enroll",
        json={
            "enrollment_token": created.json()["token"],
            "worker_name": display_name,
            "hostname": hostname,
            "capabilities": {"has_nvenc": False, "video_encoder": "libx264"},
            "storage_cap_bytes": 1,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    return enrolled.json()["worker_id"]


def fleet_worker(harness, worker_id: str) -> dict:
    fleet = harness.client.get("/fleet", auth=harness.admin_auth).json()["workers"]
    return next(entry["worker"] for entry in fleet if entry["worker"]["id"] == worker_id)


def rename(harness, worker_id: str, name: str):
    return harness.client.patch(
        f"/admin/workers/{worker_id}", json={"name": name}, auth=harness.admin_auth
    )


def test_enrollment_keeps_the_typed_name_and_the_host_name_apart(harness):
    # The volunteer typed "Notebook da Maria" over the prefilled host name.
    worker_id = enroll_with_hostname(harness, "Notebook da Maria", "DESKTOP-8F3K2LQ")

    worker = fleet_worker(harness, worker_id)

    # The display name is `name`; the host name is kept beside it.
    assert worker["name"] == "Notebook da Maria"
    assert worker["hostname"] == "DESKTOP-8F3K2LQ"


def test_an_admin_renames_a_worker_and_the_host_name_stays(harness):
    worker_id = enroll_with_hostname(harness, "DESKTOP-8F3K2LQ", "DESKTOP-8F3K2LQ")

    response = rename(harness, worker_id, "  PC da sala 3  ")

    # The answer is the renamed worker; surrounding spaces are dropped.
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "PC da sala 3"
    # Everyone reading the fleet sees the new name, and still the host name.
    worker = fleet_worker(harness, worker_id)
    assert (worker["name"], worker["hostname"]) == ("PC da sala 3", "DESKTOP-8F3K2LQ")


def test_a_rename_is_audited_with_the_names_before_and_after(harness):
    worker = harness.enroll(WorkerRole.WORKER, "DESKTOP-8F3K2LQ")

    rename(harness, worker.worker_id, "PC da Maria")

    # The Workers page's "Últimas alterações" box reads this.
    audit = harness.client.get("/admin/audit?area=workers", auth=harness.admin_auth).json()
    [event] = audit["events"]
    assert event["kind"] == "worker_renamed"
    assert event["worker_id"] == worker.worker_id
    assert event["details"]["before"]["name"] == "DESKTOP-8F3K2LQ"
    assert event["details"]["after"]["name"] == "PC da Maria"


def test_renaming_to_the_same_name_changes_nothing_and_logs_nothing(harness):
    worker = harness.enroll(WorkerRole.WORKER, "PC da Maria")

    assert rename(harness, worker.worker_id, "PC da Maria").status_code == 200

    audit = harness.client.get("/admin/audit?area=workers", auth=harness.admin_auth).json()
    assert audit["events"] == []


def test_a_blank_name_and_an_unknown_worker_are_refused(harness):
    worker = harness.enroll(WorkerRole.WORKER, "PC da Maria")

    # Only spaces is no name at all → 400 validation_error.
    blank = rename(harness, worker.worker_id, "   ")
    assert blank.status_code == 400
    assert blank.json()["error"]["code"] == "validation_error"
    # An id nobody has → 404.
    assert rename(harness, "0" * 32, "Fantasma").status_code == 404


def test_only_an_admin_may_rename(harness):
    worker = harness.enroll(WorkerRole.WORKER, "PC da Maria")

    # A reviewer's session is refused (403), and so is the worker's own token.
    as_reviewer = harness.client.patch(
        f"/admin/workers/{worker.worker_id}", json={"name": "x"}, auth=harness.reviewer_auth
    )
    as_worker = harness.client.patch(
        f"/admin/workers/{worker.worker_id}", json={"name": "x"}, headers=worker.headers
    )
    assert as_reviewer.status_code == 403
    assert as_worker.status_code in (401, 403)
