"""Revoked workers and used tokens are out of the way (LIVESTACK-42 item 9).

After a day of real use João's Workers and Cadastro de PCs pages were
mostly history: revoked PCs and tokens already spent. Now:

* Frota (fleet page) shows only workers that still work for us;
* Admin → Workers lists revoked workers only in a folded "Revogados"
  section at the bottom;
* Admin → Cadastro de PCs keeps unused tokens on top and folds used,
  expired and revoked ones into a section below.

A "folded section" is an HTML <details> element: the browser shows only its
<summary> line until someone clicks it. The tests split the page at that
element to tell "in the main list" from "folded away".
"""

from datetime import timedelta

from shared.models import WorkerRole
from tests.review_ui.admin_browser import post_form
from tests.review_ui.sample_data import T0, make_fleet


def split_at_folded_section(html: str, section_id: str) -> tuple[str, str]:
    """(the page before the folded section, the folded section itself)."""
    marker = f'<details class="folded-section" id="{section_id}">'
    if marker not in html:
        return html, ""
    before, after = html.split(marker, 1)
    return before, after.split("</details>", 1)[0]


def revoke(admin, worker_id: str) -> None:
    response = post_form(admin, f"/review/admin/workers/{worker_id}/revoke")
    assert response.status_code == 303, response.text


# --- Admin → Workers ---


def test_a_revoked_worker_moves_to_the_folded_revogados_section(admin, harness):
    # Two enrolled PCs; the admin revokes one of them.
    harness.enroll(WorkerRole.WORKER, "pc-da-maria")
    gone = harness.enroll(WorkerRole.WORKER, "pc-antigo")
    revoke(admin, gone.worker_id)

    html = admin.get("/review/admin/workers").text
    main_list, folded = split_at_folded_section(html, "revoked-workers")

    # The working PC is in the main list; the revoked one only in the fold,
    # whose summary line counts it.
    assert "pc-da-maria" in main_list
    assert "pc-antigo" not in main_list
    assert "pc-antigo" in folded
    assert "Revogados (1)" in folded
    # A revoked worker has nothing left to revoke.
    assert "/revoke" not in folded


def test_without_revoked_workers_there_is_no_folded_section(admin, harness):
    harness.enroll(WorkerRole.WORKER, "pc-da-maria")

    html = admin.get("/review/admin/workers").text

    assert 'id="revoked-workers"' not in html


# --- Admin → Cadastro de PCs ---


def create_token(admin, volunteer: str) -> None:
    response = post_form(
        admin,
        "/review/admin/enrollment/create",
        volunteer_name=volunteer,
        role="worker",
        valid_for_hours="24",
    )
    assert response.status_code == 200


def test_unused_tokens_stay_on_top_and_the_rest_is_folded(admin, harness):
    # Three tokens: one still unused, one spent by an enrollment, one revoked.
    create_token(admin, "Maria Ainda Nao Usou")
    harness.enroll(WorkerRole.WORKER, "pc-do-pedro")  # mints and spends a token
    create_token(admin, "Jose Revogado")
    tokens = harness.client.get("/admin/enrollment-tokens", headers=harness.admin_headers)
    revoked = next(t for t in tokens.json()["tokens"] if t["volunteer_name"] == "Jose Revogado")
    post_form(admin, f"/review/admin/enrollment/{revoked['token_id']}/revoke")

    html = admin.get("/review/admin/enrollment").text
    main_list, folded = split_at_folded_section(html, "other-tokens")

    assert "Maria Ainda Nao Usou" in main_list
    assert "Jose Revogado" not in main_list
    assert "Jose Revogado" in folded
    assert "Usado" in folded  # the spent token's badge
    assert "Usados, expirados e revogados (2)" in folded


def test_an_expired_token_is_folded_but_can_still_be_revoked(admin, harness):
    # A token valid for 1 hour, then the clock moves two hours on: it expired
    # unused. (Two hours, not days: the admin's session itself ends after
    # 12 idle hours.)
    response = harness.client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": "Ana Atrasada", "role": "worker", "valid_for_hours": 1},
        headers=harness.admin_headers,
    )
    assert response.status_code == 201
    harness.clock.advance(timedelta(hours=2).total_seconds())

    html = admin.get("/review/admin/enrollment").text
    main_list, folded = split_at_folded_section(html, "other-tokens")

    assert "Ana Atrasada" not in main_list
    assert "Expirado" in folded
    # Revoking an expired token is still allowed (so nobody can revive it).
    assert "/revoke" in folded
    # With no unused token left, the main list says so in words.
    assert "Nenhum token por usar." in main_list


# --- Frota (fleet page, fake orchestrator) ---


def test_the_fleet_page_leaves_revoked_workers_out(ui, fake_orchestrator):
    # The sample fleet (joao-desktop, maria-notebook) plus a revoked PC.
    fleet = make_fleet()
    gone = fleet.workers[0].model_copy(
        update={
            "worker": fleet.workers[0].worker.model_copy(
                update={"id": "w-gone", "name": "pc-revogado", "revoked_at": T0}
            )
        }
    )
    fake_orchestrator.fleet = fleet.model_copy(update={"workers": [*fleet.workers, gone]})

    html = ui.get("/review/fleet").text

    assert "joao-desktop" in html
    assert "pc-revogado" not in html
    # Its events are not even asked for.
    asked = [
        dict(call.query)["worker_id"] for call in fake_orchestrator.requests_to("GET", "/events")
    ]
    assert "w-gone" not in asked
