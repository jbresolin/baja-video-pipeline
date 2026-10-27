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
from review_ui.queue_view import distinct_provas
from review_ui.request_context import orchestrator_for, render_page
from review_ui.settings import FLEET_POLL_SECONDS

router = APIRouter()


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


def _fleet_context(request: Request) -> dict:
    fleet = orchestrator_for(request).fleet()
    return {
        "fleet": fleet,
        "worker_rows": build_worker_rows(fleet),
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
