"""The queue page (/review/) and the fragment it polls for fresh rows."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from review_ui.queue_view import (
    STATES_IN_PIPELINE_ORDER,
    QueueFilter,
    apply_local_filters,
    distinct_provas,
    parse_queue_filter,
)
from review_ui.request_context import orchestrator_for, render_page
from review_ui.settings import QUEUE_POLL_SECONDS

router = APIRouter()


def _queue_context(request: Request, queue_filter: QueueFilter) -> dict:
    all_items = orchestrator_for(request).review_queue(queue_filter.states)
    return {
        "items": apply_local_filters(all_items, queue_filter),
        # Prova choices come from the unfiltered list, so picking one prova
        # does not make the others disappear from the menu.
        "prova_options": distinct_provas([item.prova for item in all_items]),
        "filter": queue_filter,
    }


# Sync handlers on purpose: FastAPI runs them in a thread pool, so the
# blocking orchestrator calls never stall other requests.
@router.get("/review", include_in_schema=False)
def review_root() -> RedirectResponse:
    return RedirectResponse("/review/", status_code=308)


@router.get("/review/", response_class=HTMLResponse)
def queue_page(
    request: Request,
    state: list[str] = Query(default_factory=list),
    prova: str | None = None,
    flagged: str | None = None,
) -> HTMLResponse:
    queue_filter = parse_queue_filter(state, prova, flagged)
    context = _queue_context(request, queue_filter)
    context.update(
        active_nav="queue",
        all_states=STATES_IN_PIPELINE_ORDER,
        poll_seconds=QUEUE_POLL_SECONDS,
    )
    return render_page(request, "queue.html", context)


@router.get("/review/partials/queue", response_class=HTMLResponse)
def queue_rows_partial(
    request: Request,
    state: list[str] = Query(default_factory=list),
    prova: str | None = None,
    flagged: str | None = None,
) -> HTMLResponse:
    """Only the rows, for the page's auto-refresh (same filters as the page)."""
    queue_filter = parse_queue_filter(state, prova, flagged)
    return render_page(request, "_queue_rows.html", _queue_context(request, queue_filter))
