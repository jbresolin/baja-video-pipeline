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


# --- "Tornar master" (LIVESTACK-42 item 8) ---


def workers_page_row(html: str, name: str) -> str:
    """The <tr> of the main Workers table whose name field holds `name`."""
    for row in html.split("<tr>")[1:]:
        if f'name="name" value="{name}"' in row:
            return row.split("</tr>", 1)[0]
    raise AssertionError(f"no row for {name}")


def test_only_plain_workers_get_the_button_and_it_asks_first_naming_the_master(admin, harness):
    harness.enroll(WorkerRole.MASTER, "joao-desktop")
    maria = harness.enroll(WorkerRole.WORKER, "pc-da-maria")
    harness.enroll(WorkerRole.STUDIO, "estudio")

    html = admin.get("/review/admin/workers").text

    # The volunteer's row has the button, inside a form that asks first and
    # says who stops being the master.
    maria_row = workers_page_row(html, "pc-da-maria")
    assert f'action="/review/admin/workers/{maria.worker_id}/make-master"' in maria_row
    assert "Tornar master" in maria_row
    assert "joao-desktop deixa de ser o master" in maria_row
    # The master itself and the studio have no such button...
    assert "make-master" not in workers_page_row(html, "joao-desktop")
    studio_row = workers_page_row(html, "estudio")
    assert "make-master" not in studio_row
    # ...and the studio's row says why it cannot be promoted.
    assert "O estúdio não vira master" in studio_row


def test_pressing_the_button_switches_the_master(admin, harness):
    master = harness.enroll(WorkerRole.MASTER, "joao-desktop")
    maria = harness.enroll(WorkerRole.WORKER, "pc-da-maria")

    page = follow(admin, post_form(admin, f"/review/admin/workers/{maria.worker_id}/make-master"))

    # The page says what happens next, and the change is in the audit box.
    assert "Master trocado." in page.text
    assert "master trocado" in page.text
    # The orchestrator now tells each PC its new role on the next heartbeat.
    assert harness.heartbeat(maria, role="worker")["role"] == "master"
    assert harness.heartbeat(master, role="master")["role"] == "worker"
    # The new page offers the button on the old master's row instead.
    assert "make-master" in workers_page_row(page.text, "joao-desktop")


def test_a_refused_promotion_shows_the_reason(admin, harness):
    # A crafted post for the studio (the page never offers it).
    studio = harness.enroll(WorkerRole.STUDIO, "estudio")

    page = post_form(admin, f"/review/admin/workers/{studio.worker_id}/make-master")

    assert page.status_code == 409
    assert "enroll that PC again with a master token" in page.text
