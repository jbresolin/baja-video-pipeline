"""Admin pages: Provas (add, rename, reorder, deactivate) and Equipes (teams table + CSV)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from review_ui.admin_view import (
    admin_context,
    apply_change,
    audit_lines,
    error_text,
    form_text,
)
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError
from review_ui.request_context import admin_client_for, render_page
from shared.models import (
    AdminArea,
    ImportTeamsRequest,
    SaveTeamRequest,
    TeamsImportMode,
    UpdateProvaRequest,
)

router = APIRouter(prefix="/review/admin")

PROVAS_PATH = "/review/admin/provas"
TEAMS_PATH = "/review/admin/teams"


# --- Provas ---


def _provas_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    context = admin_context(
        request, "provas", provas=admin.provas(), audit=audit_lines(admin.audit(AdminArea.PROVAS))
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
    """The ↑/↓ buttons: swap with the neighbour, then save the whole order."""
    admin = admin_client_for(request)
    current = [prova.id for prova in admin.provas()]
    if prova_id not in current:
        return _provas_page(request, "Essa prova não existe mais.", 404)
    new_order = moved(current, prova_id, form.get("direction", "up"))
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


def _teams_page(
    request: Request,
    error: str | None = None,
    status_code: int = 200,
    import_result=None,
):
    admin = admin_client_for(request)
    context = admin_context(
        request,
        "teams",
        teams=admin.teams(),
        import_result=import_result,
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
    """Add a car (the form above the table) or save an inline edit (a row's form)."""
    try:
        car_number = int(form.get("car_number", ""))
        body = SaveTeamRequest(
            team_name=form_text(form, "team_name") or "", school=form_text(form, "school")
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


@router.post("/teams/import")
def import_teams(request: Request, form: dict = Depends(read_form)) -> Response:
    """The CSV arrives as text (js/admin.js copies the chosen file into the textarea).

    The page is shown again (not redirected) so it can list the skipped lines.
    """
    try:
        body = ImportTeamsRequest(
            csv_text=form.get("csv_text", ""), mode=TeamsImportMode(form.get("mode", "merge"))
        )
    except ValueError:
        return _teams_page(request, "Escolha um arquivo CSV e o modo (substituir ou mesclar).", 400)
    try:
        result = admin_client_for(request).import_teams(body)
    except OrchestratorError as error:
        if error.status_code in (401, 403):
            raise
        return _teams_page(request, error_text(error), error.status_code)
    return _teams_page(request, import_result=result)
