"""Syncing provas and teams from the results API (orchestrator/domain/results_sync.py).

The operator sets the event id ("26BR") on the Settings page; that syncs at
once, the Sync button syncs again, and the background schedule every 6 h.
A sync renames and adds, and marks rows the API stopped listing inactive;
it never deletes anything, because jobs may use it.

The fake results API starts with the documented 26BR example: provas SGE
"Segurança Estática", END "Enduro", EPN "Penalizações Enduro"; teams 1
"BAJA UFMG" and 2 "Equipe FEI Baja". Tests edit the fake to play what the
API says later.
"""

from orchestrator.domain.results_sync import sync_if_due
from orchestrator.testing.scenarios import (
    ask_next,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import ProcessTask, TaskKind, WorkerRole
from tests.worker.overlay_golden import GOLDEN_TEXT
from worker.tasks.process import overlay_text_for


def set_event(harness, event_id="26BR"):
    response = harness.client.patch(
        "/admin/settings", json={"event_id": event_id}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    return response.json()


def sync(harness):
    return harness.client.post("/admin/results-sync", auth=harness.admin_auth)


def provas(harness) -> list[dict]:
    return harness.client.get("/provas", auth=harness.reviewer_auth).json()["provas"]


def test_setting_the_event_syncs_its_provas_and_teams_at_once(harness):
    page = set_event(harness)
    report = page["status"]["results_sync"]
    assert report["ok"] is True
    assert (report["provas_added"], report["teams_added"]) == (3, 2)
    assert [(p["name"], p["slug"], p["source"], p["external_id"]) for p in provas(harness)] == [
        ("Segurança Estática", "seguranca-estatica", "results_api", "SGE"),
        ("Enduro", "enduro", "results_api", "END"),
        ("Penalizações Enduro", "penalizacoes-enduro", "results_api", "EPN"),
    ]
    team = harness.client.get("/teams/2", auth=harness.reviewer_auth).json()
    assert (team["team_name"], team["short_name"]) == ("Equipe FEI Baja", "FEI")


def test_a_sync_renames_adds_and_deactivates_without_deleting(harness):
    set_event(harness)
    api_provas = harness.results_api.provas_of()
    api_provas[1]["name"] = "Enduro de Resistência"  # END renamed
    del api_provas[2]  # EPN gone
    api_provas.append({"id": "SPR", "name": "Super Prime", "type": "tournament"})  # new
    del harness.results_api.teams_of()[0]  # car 1 gone
    harness.results_api.add_team(3, "Equipe Três", "Três")  # new car

    report = sync(harness).json()

    assert (report["provas_added"], report["provas_renamed"], report["provas_deactivated"]) == (
        1,
        1,
        1,
    )
    by_id = {p["external_id"]: p for p in provas(harness)}
    # Renamed, same folder.
    assert (by_id["END"]["name"], by_id["END"]["slug"]) == ("Enduro de Resistência", "enduro")
    # Gone from the API: still there, inactive.
    assert by_id["EPN"]["active"] is False
    assert by_id["SPR"]["active"] is True
    # Teams: car 1 kept (marked not in the event), car 3 added.
    listed = harness.client.get("/admin/teams", auth=harness.admin_auth).json()["teams"]
    teams = {team["car_number"]: team for team in listed}
    assert teams[1]["in_source"] is False
    assert teams[3]["short_name"] == "Três"
    assert (report["teams_added"], report["teams_deactivated"]) == (1, 1)


def test_a_rename_by_the_api_relabels_jobs_and_keeps_their_clip_folder(harness):
    set_event(harness)
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro")
    harness.results_api.provas_of()[1]["name"] = "Enduro 4h"
    sync(harness)
    assert harness.job(job_id)["prova"] == "Enduro 4h"


def test_a_prova_that_comes_back_is_active_again_but_an_admin_hide_stays(harness):
    set_event(harness)
    by_id = {p["external_id"]: p for p in provas(harness)}
    # The admin hides "Penalizações Enduro" (not a clip category).
    harness.client.patch(
        f"/admin/provas/{by_id['EPN']['id']}", json={"active": False}, auth=harness.admin_auth
    )
    # END disappears from the API for one sync, then comes back.
    saved = harness.results_api.provas_of().pop(1)
    sync(harness)
    harness.results_api.provas_of().insert(1, saved)
    sync(harness)
    by_id = {p["external_id"]: p for p in provas(harness)}
    assert by_id["END"]["active"] is True  # the sync had hidden it; back
    assert by_id["EPN"]["active"] is False  # the admin hid it; the sync leaves it


def test_a_manual_prova_with_the_same_folder_is_adopted_not_duplicated(harness):
    # "Enduro" was typed before the event was set (or backfilled from jobs).
    manual = harness.client.post(
        "/admin/provas", json={"name": "Enduro"}, auth=harness.admin_auth
    ).json()
    set_event(harness)
    enduro = [p for p in provas(harness) if p["slug"] == "enduro"]
    assert len(enduro) == 1
    assert (enduro[0]["id"], enduro[0]["source"]) == (manual["id"], "results_api")


def test_manual_provas_stay_and_come_after_the_synced_ones(harness):
    harness.client.post("/admin/provas", json={"name": "Abertura"}, auth=harness.admin_auth)
    set_event(harness)
    names = [p["name"] for p in provas(harness)]
    assert names[-1] == "Abertura"
    assert names[:3] == ["Segurança Estática", "Enduro", "Penalizações Enduro"]


def test_a_synced_prova_cannot_be_renamed_by_hand(harness):
    set_event(harness)
    enduro = next(p for p in provas(harness) if p["external_id"] == "END")
    response = harness.client.patch(
        f"/admin/provas/{enduro['id']}", json={"name": "Outro"}, auth=harness.admin_auth
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "conflict")


def test_a_wrong_event_id_is_reported_and_nothing_changes(harness):
    page = set_event(harness, "26XX")
    report = page["status"]["results_sync"]
    assert (report["ok"], report["error_code"]) == (False, "not_found")
    assert provas(harness) == []
    # The Sync button says so with a 422 the admin can act on.
    assert sync(harness).status_code == 422


def test_a_rate_limit_is_a_503_and_the_report_keeps_it(harness):
    set_event(harness)
    harness.results_api.rate_limited_for_s = 12
    response = sync(harness)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "upstream_error")
    assert response.json()["error"]["details"]["error_code"] == "rate_limited"


def test_without_an_event_the_sync_button_explains(harness):
    response = sync(harness)
    assert response.status_code == 400


def test_the_schedule_syncs_every_6_hours_and_retries_failures_sooner(harness):
    # No event: nothing to do.
    assert sync_if_due(harness.context) is None
    set_event(harness)  # synced now
    harness.clock.advance(5 * 3600)
    assert sync_if_due(harness.context) is None  # not 6 h yet
    harness.clock.advance(3600)
    assert sync_if_due(harness.context).ok is True  # 6 h: synced
    # A failed sync is tried again after 15 minutes, not 6 hours.
    harness.results_api.rate_limited_for_s = 5
    harness.clock.advance(6 * 3600)
    assert sync_if_due(harness.context).ok is False
    harness.results_api.rate_limited_for_s = None
    harness.clock.advance(15 * 60)
    assert sync_if_due(harness.context).ok is True


def test_syncs_show_up_in_the_provas_and_teams_pages_latest_changes(harness):
    set_event(harness)
    for area in ("provas", "teams"):
        events = harness.client.get(f"/admin/audit?area={area}", auth=harness.admin_auth).json()[
            "events"
        ]
        assert events[0]["kind"] == "results_synced"
        assert events[0]["actor"] == "admin:joao"


def test_the_overlay_uses_the_teams_short_name(harness):
    set_event(harness)
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    job = review_to_ready(harness, job_id, prova="Enduro", car_number=2)
    # The job keeps the full name (review UI, latest.m3u)...
    assert job["team_name"] == "Equipe FEI Baja"
    # ...and the render task's overlay text is the short one.
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["team_name"] == "FEI"


def test_the_overlay_role_pill_gets_the_schools_short_name(harness):
    # LIVESTACK-38 item 8: the role pill reads "ENDURO · PASSAGEM 3 · UFMG".
    # The school's short name comes from the API's `school_short` (car 1
    # is BAJA UFMG in the fake API) and is synced into the teams table.
    set_event(harness)
    teams = harness.client.get("/admin/teams", auth=harness.admin_auth).json()["teams"]
    assert next(t for t in teams if t["car_number"] == 1)["school_short"] == "UFMG"
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro", car_number=1, passagem=3)

    # The render task carries it to the worker, which draws it.
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["school_short"] == "UFMG"
    # And what the worker draws from this task is exactly the golden
    # image's text (tests/worker/overlay_golden.py), so the approved
    # picture is what a synced team really looks like on a clip.
    task = ProcessTask.model_validate(offer)
    assert overlay_text_for(task) == GOLDEN_TEXT


def test_a_car_without_a_school_short_name_renders_without_one(harness):
    # A team the API lists without `school_short`: the pill shows prova and
    # passagem only, instead of an empty "·" at the end.
    harness.results_api.add_team(77, "Equipe Sem Sigla")
    set_event(harness)  # setting the event runs the first sync
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro", car_number=77)

    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["school_short"] is None
