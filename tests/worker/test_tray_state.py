"""The tray icon's decisions (worker.tray.state): colour, tooltip, menu, notifications.

The tray itself is a Windows program, but everything it *decides* lives in
a module with no GUI code, so we can test it here. Each test builds the
status document the worker's GET /api/status would return (see
`status_document` below), plus the Windows service state, and checks what
the volunteer would see.
"""

import pytest

from worker.tray.state import (
    Colour,
    MenuState,
    ServiceState,
    Toast,
    ToastTracker,
    view_for,
)


def status_document(**overrides) -> dict:
    """A healthy, enrolled, idle worker that talked to the server just now."""
    document = {
        "enrolled": True,
        "enrollment_refused": False,
        "connection_step_pending": False,
        "paused": False,
        "last_contact_at": "2026-11-04T14:00:00Z",
        "unreachable_for_s": None,
        "tasks": [],
        "storage": {"used_bytes": 1, "cap_bytes": 100, "near_cap": False},
        "held_raws": 3,
    }
    document.update(overrides)
    return document


def running(document: dict):
    """What the tray shows for `document` while the service runs."""
    return view_for(document, ServiceState.RUNNING, was_running=True)


# --- colours and tooltips, one per state -----------------------------------


def test_idle_and_connected_is_blue():
    view = running(status_document())
    assert view.colour == Colour.BLUE
    assert view.tooltip == "Baja Worker: conectado, aguardando vídeos"


def test_a_running_task_is_green_and_counts_videos():
    # Two tasks about two different videos → "2 vídeos"; the tooltip says
    # how many, as in the brief's example "processando 1 vídeo".
    tasks = [{"kind": "process", "job_id": "a"}, {"kind": "download_raw", "job_id": "b"}]
    assert running(status_document(tasks=tasks)).tooltip == "Baja Worker: processando 2 vídeos"
    one = running(status_document(tasks=tasks[:1]))
    assert one.colour == Colour.GREEN
    assert one.tooltip == "Baja Worker: processando 1 vídeo"


def test_paused_is_yellow_and_says_it_is_finishing_a_task():
    # Paused while a task is still running: it finishes that task first.
    finishing = running(status_document(paused=True, tasks=[{"kind": "process", "job_id": "a"}]))
    assert finishing.colour == Colour.YELLOW
    assert finishing.headline == "pausando (terminando 1 vídeo)"
    # Once that task is done: plain "pausado".
    assert running(status_document(paused=True)).headline == "pausado"


def test_connection_step_pending_is_yellow():
    view = running(status_document(connection_step_pending=True, unreachable_for_s=600))
    # Yellow, not red, even though the server is unreachable: this is a
    # step the volunteer still has to do, not an outage.
    assert view.colour == Colour.YELLOW
    assert view.headline == "conexão não concluída"


def test_waiting_for_the_enrollment_token_is_yellow():
    view = running(status_document(enrolled=False, last_contact_at=None))
    assert view.colour == Colour.YELLOW
    assert view.headline == "aguardando o token de inscrição"


def test_unreachable_for_more_than_a_minute_is_red_but_a_short_blip_is_not():
    # 30 s without contact: probably a blip, still blue.
    assert running(status_document(unreachable_for_s=30)).colour == Colour.BLUE
    # Over a minute: red, "sem conexão com o servidor".
    view = running(status_document(unreachable_for_s=61))
    assert view.colour == Colour.RED
    assert view.tooltip == "Baja Worker: sem conexão com o servidor"


@pytest.mark.parametrize(
    ("overrides", "headline"),
    [
        ({"storage": {"near_cap": True}}, "disco quase cheio (limite de armazenamento)"),
        ({"enrollment_refused": True, "enrolled": False}, "token de inscrição recusado"),
    ],
)
def test_problems_the_volunteer_must_act_on_are_red(overrides, headline):
    view = running(status_document(**overrides))
    assert view.colour == Colour.RED
    assert view.headline == headline


def test_a_problem_wins_over_paused_and_busy():
    # Red must never hide behind yellow or green: a full disk while paused
    # and busy is still shown as the disk problem.
    document = status_document(
        paused=True, tasks=[{"kind": "process", "job_id": "a"}], storage={"near_cap": True}
    )
    assert running(document).colour == Colour.RED


def test_service_stopped_by_the_volunteer_is_grey():
    view = view_for(None, ServiceState.STOPPED, stopped_by_us=True, was_running=True)
    assert view.colour == Colour.GREY
    assert view.tooltip == "Baja Worker: desligado"


def test_service_stopping_by_itself_is_red():
    # The tray saw it running, nobody chose "Desligar", now it is stopped.
    view = view_for(None, ServiceState.STOPPED, stopped_by_us=False, was_running=True)
    assert view.colour == Colour.RED
    assert view.headline == "o serviço parou inesperadamente"


def test_service_already_stopped_when_the_tray_starts_is_grey():
    # The tray never saw it running (e.g. switched off before this logon):
    # we cannot tell it crashed, so grey rather than a false alarm.
    assert view_for(None, ServiceState.STOPPED).colour == Colour.GREY


def test_service_running_but_page_not_answering_yet_is_yellow_starting():
    view = view_for(None, ServiceState.RUNNING, was_running=True)
    assert view.colour == Colour.YELLOW
    assert view.headline == "iniciando…"


# --- the menu ------------------------------------------------------------------


def test_menu_while_running_offers_pause_and_stop():
    assert running(status_document()).menu == MenuState(
        open_panel=True, pause=True, resume=False, stop=True, start=False,
        finish_connection=False, exit_icon=True,
    )  # fmt: skip


def test_menu_while_paused_offers_resume_instead_of_pause():
    menu = running(status_document(paused=True)).menu
    assert menu.resume and not menu.pause


def test_finish_connection_is_shown_only_while_the_join_is_pending():
    assert running(status_document(connection_step_pending=True)).menu.finish_connection
    assert not running(status_document()).menu.finish_connection


def test_menu_while_stopped_offers_start_and_no_panel():
    # The panel is served by the service, so "Abrir painel" would not load.
    menu = view_for(None, ServiceState.STOPPED, stopped_by_us=True).menu
    assert menu.start and not menu.stop and not menu.open_panel and not menu.pause
    # "Sair do ícone" is always there.
    assert menu.exit_icon


# --- notifications, each only once ---------------------------------------------


def test_each_notification_fires_once_while_its_condition_lasts():
    tracker = ToastTracker()
    full = running(status_document(storage={"near_cap": True}))
    # First time the disk is near the cap: one toast.
    assert tracker.new_toasts(full) == [Toast.DISK_NEAR_CAP]
    # Next polls, still near the cap: nothing new (no spam every 3 s).
    assert tracker.new_toasts(full) == []
    assert tracker.new_toasts(full) == []
    # Space freed, then full again later: a new toast, because it is news.
    assert tracker.new_toasts(running(status_document())) == []
    assert tracker.new_toasts(full) == [Toast.DISK_NEAR_CAP]


def test_notifications_only_for_things_to_act_on_never_per_job():
    tracker = ToastTracker()
    # Busy with videos, paused, resumed: none of this deserves a toast.
    for document in (
        status_document(tasks=[{"kind": "process", "job_id": "a"}]),
        status_document(paused=True),
        status_document(),
    ):
        assert tracker.new_toasts(running(document)) == []


def test_the_four_actionable_notifications():
    tracker = ToastTracker()
    pending = running(status_document(connection_step_pending=True))
    assert tracker.new_toasts(pending) == [Toast.CONNECTION_STEP_PENDING]
    refused = running(status_document(enrolled=False, enrollment_refused=True))
    assert tracker.new_toasts(refused) == [Toast.ENROLLMENT_REFUSED]
    crashed = view_for(None, ServiceState.STOPPED, was_running=True)
    assert tracker.new_toasts(crashed) == [Toast.SERVICE_STOPPED_UNEXPECTEDLY]


def test_stopping_the_service_on_purpose_raises_no_notification():
    tracker = ToastTracker()
    tracker.new_toasts(running(status_document()))
    stopped = view_for(None, ServiceState.STOPPED, stopped_by_us=True, was_running=True)
    assert tracker.new_toasts(stopped) == []


# --- LIVESTACK-41: broken worker.json, revoked, tailnet, studio folder ------


def test_a_broken_worker_json_is_red_and_notifies():
    problem = {"file": "C:\\BajaWorker\\worker.json", "message": "…", "hint": "…", "line": 3}
    view = running(status_document(enrolled=False, config_problem=problem))
    assert view.colour == Colour.RED
    assert view.tooltip == "Baja Worker: erro no arquivo de configuração (worker.json)"
    assert Toast.CONFIG_PROBLEM in view.alerts


def test_a_revoked_worker_is_red_and_offers_registrar_de_novo():
    view = running(status_document(identity_rejected=True, can_re_register=True))
    assert view.colour == Colour.RED
    assert view.tooltip == "Baja Worker: este PC foi removido da equipe Baja"
    assert view.menu.re_register
    assert Toast.IDENTITY_REJECTED in view.alerts


def test_waiting_for_the_tailnet_is_yellow_and_offers_both_fixes():
    problem = {"kind": "not_connected", "saved_address": "100.64.0.9", "current_address": None}
    view = running(status_document(tailnet_problem=problem, can_re_register=True))
    assert view.colour == Colour.YELLOW
    assert view.tooltip == "Baja Worker: não conectado à rede Baja"
    assert view.menu.finish_connection and view.menu.re_register
    assert Toast.NOT_ON_BAJA_TAILNET in view.alerts


def test_tailscale_still_starting_after_a_boot_colours_but_does_not_notify():
    # The first seconds after a boot are normal: no notification for them.
    problem = {"kind": "tailscale_not_answering", "saved_address": "100.64.0.9"}
    view = running(status_document(tailnet_problem=problem))
    assert view.colour == Colour.YELLOW
    assert Toast.NOT_ON_BAJA_TAILNET not in view.alerts


def test_a_studio_without_its_clip_folder_is_yellow():
    view = running(status_document(phase="studio_folder"))
    assert view.colour == Colour.YELLOW
    assert view.tooltip == "Baja Worker: falta escolher a pasta dos clipes"


def test_registrar_de_novo_is_not_offered_before_enrollment():
    view = running(status_document(enrolled=False, can_re_register=False))
    assert not view.menu.re_register
