"""Route: GET /metrics, Prometheus text format, admin token only (docs/api.md §7.5)."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from orchestrator.api.auth import get_context, require_admin
from orchestrator.context import OrchestratorContext
from orchestrator.domain.metrics import PROMETHEUS_CONTENT_TYPE, render_metrics

router = APIRouter()


@router.get(
    "/metrics",
    tags=["operations"],
    response_class=PlainTextResponse,
    dependencies=[Depends(require_admin)],
)
def metrics(context: OrchestratorContext = Depends(get_context)) -> PlainTextResponse:
    return PlainTextResponse(render_metrics(context), media_type=PROMETHEUS_CONTENT_TYPE)
