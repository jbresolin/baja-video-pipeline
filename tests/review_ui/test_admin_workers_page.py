"""Admin → Workers: display names (LIVESTACK-42 item 7).

These run the review UI against the real orchestrator (admin_browser.py),
so a rename typed on the page is checked where it matters: in what the
orchestrator then answers to everyone.
"""

from shared.models import WorkerRole
from tests.review_ui.admin_browser import follow, post_form
from tests.review_ui.sample_data import MASTER_ID, make_fleet


def enroll_with_hostname(harness, display_name: str, hostname: str) -> str:
    """Enroll a PC the way the worker app does (display name + host name)."""
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


# --- Display name ---


def test_each_worker_row_has_an_editable_name_and_shows_the_host_name(admin, harness):
    worker_id = enroll_with_hostname(harness, "Notebook da Maria", "DESKTOP-8F3K2LQ")

    html = admin.get("/review/admin/workers").text

    # The name is a text field of a small form posting to .../rename...
    assert f'action="/review/admin/workers/{worker_id}/rename"' in html
    assert 'name="name" value="Notebook da Maria"' in html
    # ...and the host name is shown under it, as secondary text.
    assert "PC: DESKTOP-8F3K2LQ" in html


def test_renaming_on_the_page_reaches_the_orchestrator(admin, harness):
    worker_id = enroll_with_hostname(harness, "DESKTOP-8F3K2LQ", "DESKTOP-8F3K2LQ")

    renamed = post_form(admin, f"/review/admin/workers/{worker_id}/rename", name="Sala 3")
    page = follow(admin, renamed)

    # The page confirms, and lists the change under "Últimas alterações".
    assert "Nome do worker salvo." in page.text
    assert "worker renomeado" in page.text
    # The orchestrator now answers the new name to everyone.
    fleet = harness.client.get("/fleet", auth=harness.admin_auth).json()["workers"]
    [entry] = [entry for entry in fleet if entry["worker"]["id"] == worker_id]
    assert entry["worker"]["name"] == "Sala 3"


def test_a_blank_name_is_explained_not_sent(admin, harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc-da-maria")

    page = post_form(admin, f"/review/admin/workers/{worker.worker_id}/rename", name="  ")

    assert page.status_code == 400
    assert "Informe um nome para o worker." in page.text


def test_when_name_and_host_name_are_the_same_the_host_name_is_not_repeated(admin, harness):
    enroll_with_hostname(harness, "DESKTOP-8F3K2LQ", "DESKTOP-8F3K2LQ")

    html = admin.get("/review/admin/workers").text

    assert "PC: DESKTOP-8F3K2LQ" not in html


def test_the_fleet_page_shows_the_display_name_with_the_host_name_below(ui, fake_orchestrator):
    # The sample master is called "joao-desktop"; give it a host name too.
    fleet = make_fleet()
    workers = [
        entry.model_copy(
            update={"worker": entry.worker.model_copy(update={"hostname": "DESKTOP-JOAO"})}
        )
        if entry.worker.id == MASTER_ID
        else entry
        for entry in fleet.workers
    ]
    fake_orchestrator.fleet = fleet.model_copy(update={"workers": workers})

    html = ui.get("/review/fleet").text

    assert "<strong>joao-desktop</strong>" in html
    assert "PC: DESKTOP-JOAO" in html
