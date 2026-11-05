"""Contract: the provas list and the admin Provas and Teams pages (LIVESTACK-37)."""

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response

from shared.models import (
    CreateProvaRequest,
    Prova,
    ProvasResponse,
    ReorderProvasRequest,
    SaveTeamRequest,
    TeamRow,
    TeamsResponse,
    UpdateProvaRequest,
)
from tools.openapi_contract.auth import (
    admin_bearer,
    error_responses,
    not_implemented,
    session_bearer,
)
from tools.openapi_contract.routes_admin_people import ADMIN_ONLY

router = APIRouter()

_CAR_NUMBER = Path(ge=0, le=9999)


@router.get(
    "/provas",
    tags=["review"],
    summary="Every prova in display order (inactive ones flagged)",
    response_model=ProvasResponse,
    dependencies=[Depends(session_bearer), Depends(admin_bearer)],
    responses=error_responses(401),
)
def list_provas() -> ProvasResponse:
    """Synced provas first (the results API's order), then manual ones. The
    review form offers the active ones. `ready`, `reprocess` and a `retry`
    back to ready need a prova from this list (active or not), else 422
    `unknown_prova`."""
    not_implemented()


@router.post(
    "/admin/provas",
    tags=["admin"],
    summary="Add a prova at the end of the list",
    response_model=Prova,
    status_code=201,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 409),
)
def create_prova(body: CreateProvaRequest) -> Prova:
    """The slug (clip folder) is made from the name and fixed from now on.
    409 `already_exists` when the name, or its folder name, is taken."""
    not_implemented()


@router.put(
    "/admin/provas/order",
    tags=["admin"],
    summary="Put the provas in a new order (every id, exactly once)",
    response_model=ProvasResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403),
)
def reorder_provas(body: ReorderProvasRequest) -> ProvasResponse:
    not_implemented()


@router.patch(
    "/admin/provas/{prova_id}",
    tags=["admin"],
    summary="Rename, deactivate or reactivate a prova",
    response_model=Prova,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 404, 409),
)
def update_prova(prova_id: int, body: UpdateProvaRequest) -> Prova:
    """A rename relabels every job that had the old name and keeps the slug,
    so clips stay in the same folder on the studio box (docs/api.md §8.1).
    A synced prova is named by the results API: renaming it is a 409
    `conflict` (activating/deactivating it is fine)."""
    not_implemented()


@router.get(
    "/admin/teams",
    tags=["admin"],
    summary="The teams table (synced from the results API, plus manual rows), by car number",
    response_model=TeamsResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403),
)
def list_teams() -> TeamsResponse:
    not_implemented()


@router.put(
    "/admin/teams/{car_number}",
    tags=["admin"],
    summary="Add a car, edit a manual one, or correct a synced one (kept across syncs)",
    response_model=TeamRow,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403),
)
def save_team(body: SaveTeamRequest, car_number: int = _CAR_NUMBER) -> TeamRow:
    not_implemented()


@router.delete(
    "/admin/teams/{car_number}",
    tags=["admin"],
    summary="Remove a manual car, or undo the correction of a synced one",
    status_code=204,
    response_class=Response,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403, 404),
)
def delete_team(car_number: int = _CAR_NUMBER) -> Response:
    not_implemented()
