"""The clips page (the delivery manifest) and the fleet page (LIVESTACK-26)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from review_ui.clips_and_fleet_view import (
    build_worker_rows,
    filter_clips_by_prova,
    jobs_by_state_in_order,
    online_count,
    studio_path,
)
from review_ui.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    OrchestratorUnavailableError,
)
from review_ui.queue_view import distinct_provas
from review_ui.request_context import orchestrator_for, render_page
from review_ui.settings import FLEET_POLL_SECONDS
from shared.models import Event

router = APIRouter()

# How many of each worker's latest events the fleet page shows.
RECENT_EVENTS_PER_WORKER = 5


@router.get("/review/clips", response_class=HTMLResponse)
def clips_page(request: Request, prova: str | None = None) -> HTMLResponse:
    manifest = orchestrator_for(request).manifest()
    chosen_prova = (prova or "").strip()
    clip_root = request.app.state.settings.studio_clip_root
    clips = filter_clips_by_prova(manifest.clips, chosen_prova)
    context = {
        "active_nav": "clips",
        # (clip, path to copy) pairs; the manifest is already newest first.
        "rows": [(clip, studio_path(clip.relative_path, clip_root)) for clip in clips],
        "prova_options": distinct_provas([clip.prova for clip in manifest.clips]),
        "chosen_prova": chosen_prova,
        "total_count": len(manifest.clips),
        "generated_at": manifest.generated_at,
    }
    return render_page(request, "clips.html", context)


def recent_events_by_worker(
    orchestrator: OrchestratorClient, worker_ids: list[str]
) -> dict[str, list[Event] | None]:
    """Each worker's latest events (GET /events?worker_id=…&limit=…), or None.

    One call per worker: a fleet is a handful of machines. The events are
    extra information, so a failure never breaks the page. After the first
    failure the remaining workers are not tried either: the cause (the
    endpoint, or the orchestrator itself) is almost surely the same for all,
    and trying each would multiply timeouts on every 10 s refresh.
    """
    events_by_worker: dict[str, list[Event] | None] = dict.fromkeys(worker_ids)
    for worker_id in worker_ids:
        try:
            events_by_worker[worker_id] = orchestrator.events(
                worker_id=worker_id, limit=RECENT_EVENTS_PER_WORKER
            )
        except (OrchestratorError, OrchestratorUnavailableError):
            break  # This worker and the ones after it stay None ("unavailable").
    return events_by_worker


def _fleet_context(request: Request) -> dict:
    orchestrator = orchestrator_for(request)
    # The fleet itself must load; its error becomes the error page.
    fleet = orchestrator.fleet()
    worker_ids = [entry.worker.id for entry in fleet.workers]
    events_by_worker = recent_events_by_worker(orchestrator, worker_ids)
    return {
        "fleet": fleet,
        "worker_rows": build_worker_rows(fleet, events_by_worker),
        "online_count": online_count(fleet),
        "job_counts": jobs_by_state_in_order(fleet),
    }


@router.get("/review/fleet", response_class=HTMLResponse)
def fleet_page(request: Request) -> HTMLResponse:
    context = _fleet_context(request)
    context.update(active_nav="fleet", poll_seconds=FLEET_POLL_SECONDS)
    return render_page(request, "fleet.html", context)


@router.get("/review/partials/fleet", response_class=HTMLResponse)
def fleet_partial(request: Request) -> HTMLResponse:
    return render_page(request, "_fleet_body.html", _fleet_context(request))
