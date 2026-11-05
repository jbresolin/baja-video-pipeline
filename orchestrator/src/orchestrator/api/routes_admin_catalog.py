"""Routes: the provas list (read by reviewers) and the admin Provas and Teams pages."""

from fastapi import APIRouter, Depends, Path, Response

from orchestrator.api.auth import get_context, require_admin, require_reviewer_or_admin
from orchestrator.context import OrchestratorContext
from orchestrator.domain import provas_admin, teams_admin
from orchestrator.domain.audit import AdminActor
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

router = APIRouter()

CAR_NUMBER = Path(ge=0, le=9999)


@router.get(
    "/provas",
    tags=["review"],
    response_model=ProvasResponse,
    dependencies=[Depends(require_reviewer_or_admin)],
)
def list_provas(context: OrchestratorContext = Depends(get_context)) -> ProvasResponse:
    return provas_admin.list_provas(context)


@router.post("/admin/provas", tags=["admin"], response_model=Prova, status_code=201)
def create_prova(
    body: CreateProvaRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Prova:
    return provas_admin.create_prova(context, actor, body)


@router.put("/admin/provas/order", tags=["admin"], response_model=ProvasResponse)
def reorder_provas(
    body: ReorderProvasRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> ProvasResponse:
    return provas_admin.reorder_provas(context, actor, body)


@router.patch("/admin/provas/{prova_id}", tags=["admin"], response_model=Prova)
def update_prova(
    prova_id: int,
    body: UpdateProvaRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Prova:
    return provas_admin.update_prova(context, actor, prova_id, body)


@router.get(
    "/admin/teams",
    tags=["admin"],
    response_model=TeamsResponse,
    dependencies=[Depends(require_admin)],
)
def list_teams(context: OrchestratorContext = Depends(get_context)) -> TeamsResponse:
    return teams_admin.list_teams(context)


@router.put("/admin/teams/{car_number}", tags=["admin"], response_model=TeamRow)
def save_team(
    body: SaveTeamRequest,
    car_number: int = CAR_NUMBER,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> TeamRow:
    return teams_admin.save_team(context, actor, car_number, body)


@router.delete("/admin/teams/{car_number}", tags=["admin"], status_code=204)
def delete_team(
    car_number: int = CAR_NUMBER,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    teams_admin.delete_team(context, actor, car_number)
    return Response(status_code=204)
