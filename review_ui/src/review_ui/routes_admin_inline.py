"""Admin toggles that change a row in place, without reloading the page (LIVESTACK-42).

On Admin → Provas (activate/deactivate, ↑/↓) and Admin → Contatos
(Permitir/Bloquear) a reload jumped back to the top of a long list after
every click. With JavaScript on, those forms (`data-inline`, static/js/
admin.js) post here instead. Each endpoint makes the same orchestrator
call as the plain form, then answers

    {"html": <the changed rows, rendered>, "message": <what was done, pt-BR>}

and the script swaps the rows in and shows the message beside the button.
The rows come from the same partial templates the full pages include, so
they are exactly what a reload would show. Without JavaScript the forms
still post to the plain routes and reload, as before.

Like every /review/api/ endpoint these need a JSON body, which a form on
another site cannot send without a CORS preflight (routes_api.py).
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from review_ui.admin_view import CONTACT_STATUS_LABELS
from review_ui.catalog import text
from review_ui.errors import UiRequestError
from review_ui.request_context import admin_client_for, render_fragment
from review_ui.routes_admin_catalog import moved
from review_ui.routes_api import read_json_body
from shared.models import CatalogSource, ContactStatus, UpdateContactRequest, UpdateProvaRequest

router = APIRouter(prefix="/review/api/admin")


def _provas_rows(request: Request, provas) -> str:
    return render_fragment(request, "admin/_provas_rows.html", {"provas": provas})


@router.post("/provas/{prova_id}/active")
async def set_prova_active(request: Request, prova_id: int) -> JSONResponse:
    """Body {"active": "1"} or {"active": "0"}, the toggle form's hidden field."""
    body = await read_json_body(request)
    active = str(body.get("active")) in ("1", "true", "True")
    admin = admin_client_for(request)
    await run_in_threadpool(admin.update_prova, prova_id, UpdateProvaRequest(active=active))
    provas = await run_in_threadpool(admin.provas)
    message = text("provas.inline.activated" if active else "provas.inline.deactivated")
    return JSONResponse({"html": _provas_rows(request, provas), "message": message})


@router.post("/provas/{prova_id}/move")
async def move_prova(request: Request, prova_id: int) -> JSONResponse:
    """Body {"direction": "up"|"down"}: swap a manual prova with its manual neighbour."""
    body = await read_json_body(request)
    direction = body.get("direction")
    if direction not in ("up", "down"):
        raise UiRequestError(400, "validation_error", text("error.validation_error"))
    admin = admin_client_for(request)
    provas = await run_in_threadpool(admin.provas)
    synced = [prova.id for prova in provas if prova.source != CatalogSource.MANUAL]
    manual = [prova.id for prova in provas if prova.source == CatalogSource.MANUAL]
    if prova_id not in manual:
        raise UiRequestError(404, "not_found", text("provas.only_manual_move"))
    new_order = synced + moved(manual, prova_id, direction)
    provas = await run_in_threadpool(admin.reorder_provas, new_order)
    return JSONResponse(
        {"html": _provas_rows(request, provas), "message": text("done.provas_reordered")}
    )


@router.post("/contacts/{phone}/status")
async def set_contact_status(request: Request, phone: str) -> JSONResponse:
    """Body {"status": "allowed"|"blocked"}: the Permitir / Bloquear buttons."""
    body = await read_json_body(request)
    if body.get("status") not in (ContactStatus.ALLOWED, ContactStatus.BLOCKED):
        raise UiRequestError(400, "validation_error", text("error.validation_error"))
    status = ContactStatus(body["status"])
    admin = admin_client_for(request)
    contact = await run_in_threadpool(
        admin.update_contact, phone, UpdateContactRequest(status=status)
    )
    provas = await run_in_threadpool(admin.provas)
    html = render_fragment(
        request,
        "admin/_contact_row.html",
        {"c": contact, "provas": provas, "status_labels": CONTACT_STATUS_LABELS},
    )
    return JSONResponse({"html": html, "message": text(f"contacts.inline.{status.value}")})
