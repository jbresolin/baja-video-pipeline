"""Admin pages: Provas and Equipes, both synced from the results API, plus manual rows."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from review_ui.admin_view import (
    admin_context,
    apply_change,
    audit_lines,
    form_text,
)
from review_ui.forms import read_form
from review_ui.request_context import admin_client_for, render_page
from shared.models import (
    AdminArea,
    CatalogSource,
    SaveTeamRequest,
    UpdateProvaRequest,
)

router = APIRouter(prefix="/review/admin")

PROVAS_PATH = "/review/admin/provas"
TEAMS_PATH = "/review/admin/teams"


def _sync_status(admin) -> dict:
    """The configured event and the last sync, for the box above both tables."""
    page = admin.settings()
    return {"event_id": page.settings.event_id, "last": page.status.results_sync}


# --- Provas ---


def _provas_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    context = admin_context(
        request,
        "provas",
        provas=admin.provas(),
        sync=_sync_status(admin),
        audit=audit_lines(admin.audit(AdminArea.PROVAS)),
    )
    context["error"] = error
    return render_page(request, "admin/provas.html", context, status_code=status_code)


def _show_provas(request: Request):
    return lambda error, status: _provas_page(request, error, status)


@router.get("/provas", response_class=HTMLResponse)
def provas_page(request: Request) -> HTMLResponse:
    return _provas_page(request)


@router.post("/provas/create")
def create_prova(request: Request, form: dict = Depends(read_form)) -> Response:
    name = form_text(form, "name") or ""
    return apply_change(
        lambda: admin_client_for(request).create_prova(name),
        PROVAS_PATH,
        "prova_created",
        _show_provas(request),
    )


def moved(prova_ids: list[int], prova_id: int, direction: str) -> list[int]:
    """The list with prova_id swapped one place up or down (unchanged at either end)."""
    order = list(prova_ids)
    index = order.index(prova_id)
    other = index - 1 if direction == "up" else index + 1
    if 0 <= other < len(order):
        order[index], order[other] = order[other], order[index]
    return order


@router.post("/provas/{prova_id}/move")
def move_prova(request: Request, prova_id: int, form: dict = Depends(read_form)) -> Response:
    """The ↑/↓ buttons of a manual prova: swap it with the manual neighbour, save the order.

    Synced provas keep the results site's order (each sync rewrites it) and
    always come first, so only the manual ones are reordered.
    """
    admin = admin_client_for(request)
    provas = admin.provas()
    synced = [prova.id for prova in provas if prova.source != CatalogSource.MANUAL]
    manual = [prova.id for prova in provas if prova.source == CatalogSource.MANUAL]
    if prova_id not in manual:
        return _provas_page(request, "Só provas manuais mudam de ordem aqui.", 404)
    new_order = synced + moved(manual, prova_id, form.get("direction", "up"))
    return apply_change(
        lambda: admin.reorder_provas(new_order),
        PROVAS_PATH,
        "provas_reordered",
        _show_provas(request),
    )


@router.post("/provas/{prova_id}")
def update_prova(request: Request, prova_id: int, form: dict = Depends(read_form)) -> Response:
    """Rename (the `name` field) or switch active on/off (the `active` field)."""
    changes = {}
    if "name" in form:
        changes["name"] = form_text(form, "name") or ""
    if "active" in form:
        changes["active"] = form["active"] == "1"
    try:
        body = UpdateProvaRequest(**changes)
    except ValueError:
        return _provas_page(request, "O nome precisa ter de 1 a 64 caracteres.", 400)
    return apply_change(
        lambda: admin_client_for(request).update_prova(prova_id, body),
        PROVAS_PATH,
        "prova_updated",
        _show_provas(request),
    )


# --- Equipes ---


def _teams_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    context = admin_context(
        request,
        "teams",
        teams=admin.teams(),
        sync=_sync_status(admin),
        audit=audit_lines(admin.audit(AdminArea.TEAMS)),
    )
    context["error"] = error
    return render_page(request, "admin/teams.html", context, status_code=status_code)


def _show_teams(request: Request):
    return lambda error, status: _teams_page(request, error, status)


@router.get("/teams", response_class=HTMLResponse)
def teams_page(request: Request) -> HTMLResponse:
    return _teams_page(request)


@router.post("/teams/save")
def save_team(request: Request, form: dict = Depends(read_form)) -> Response:
    """Add a car (the form above the table) or save a row's inline edit.

    For a synced team the orchestrator keeps the values as a correction
    that later syncs do not undo.
    """
    try:
        car_number = int(form.get("car_number", ""))
        body = SaveTeamRequest(
            team_name=form_text(form, "team_name") or "",
            short_name=form_text(form, "short_name"),
            school=form_text(form, "school"),
        )
    except ValueError:
        return _teams_page(
            request, "Número do carro (0 a 9999) e nome da equipe são obrigatórios.", 400
        )
    return apply_change(
        lambda: admin_client_for(request).save_team(car_number, body),
        TEAMS_PATH,
        "team_saved",
        _show_teams(request),
    )


@router.post("/teams/{car_number}/delete")
def delete_team(request: Request, car_number: int, form: dict = Depends(read_form)) -> Response:
    return apply_change(
        lambda: admin_client_for(request).delete_team(car_number),
        TEAMS_PATH,
        "team_deleted",
        _show_teams(request),
    )


# --- Sync with the results API (button on both pages) ---


@router.post("/sync")
def sync_from_results_api(request: Request, form: dict = Depends(read_form)) -> Response:
    """The Sincronizar button: fetch the event's provas and teams, then back to the page."""
    back_to_teams = form.get("back") == "teams"
    return apply_change(
        lambda: admin_client_for(request).sync_from_results_api(),
        TEAMS_PATH if back_to_teams else PROVAS_PATH,
        "synced",
        _show_teams(request) if back_to_teams else _show_provas(request),
    )
