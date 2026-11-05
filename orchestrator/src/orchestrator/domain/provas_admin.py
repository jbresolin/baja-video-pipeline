"""Admin Provas page: add, rename, reorder, deactivate (rules in provas_catalog.py)."""

import sqlite3
from typing import Any

from orchestrator.context import OrchestratorContext
from orchestrator.domain import provas_catalog
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.errors import ApiError, not_found, validation_error
from orchestrator.repositories import catalog as catalog_repo
from shared.layout import slugify_prova
from shared.models import (
    CreateProvaRequest,
    ErrorCode,
    Prova,
    ProvasResponse,
    ReorderProvasRequest,
    UpdateProvaRequest,
)


def list_provas(context: OrchestratorContext) -> ProvasResponse:
    with context.database.transaction() as connection:
        return ProvasResponse(provas=provas_catalog.list_provas(connection))


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    try:
        slugify_prova(cleaned)
    except ValueError as error:
        raise validation_error(str(error)) from error
    return cleaned


def _check_name_is_free(
    connection: sqlite3.Connection, name: str, own_id: int | None = None
) -> None:
    """409 if another prova already has this name, or (new provas) this folder name."""
    same_name = catalog_repo.find_prova_row_by_name(connection, name)
    if same_name is not None and same_name["id"] != own_id:
        raise ApiError(409, ErrorCode.ALREADY_EXISTS, f"prova '{name}' already exists")
    if own_id is None:
        same_slug = catalog_repo.find_prova_row_by_slug(connection, slugify_prova(name))
        if same_slug is not None:
            raise ApiError(
                409,
                ErrorCode.ALREADY_EXISTS,
                f"'{name}' would share the clip folder '{same_slug['slug']}' with prova"
                f" '{same_slug['name']}'",
                {"existing_prova_id": same_slug["id"]},
            )


def _prova_json(prova: Prova) -> dict[str, Any]:
    return prova.model_dump(mode="json")


def create_prova(
    context: OrchestratorContext, actor: AdminActor, request: CreateProvaRequest
) -> Prova:
    name = _clean_name(request.name)
    now = context.clock.now()
    with context.database.transaction() as connection:
        _check_name_is_free(connection, name)
        prova_id = catalog_repo.insert_prova(connection, name, slugify_prova(name), now)
        prova = catalog_repo.prova_from_row(catalog_repo.get_prova_row(connection, prova_id))
        record_admin_change(
            connection,
            now,
            actor,
            "prova_created",
            f"prova '{name}' added",
            after=_prova_json(prova),
        )
    return prova


def update_prova(
    context: OrchestratorContext, actor: AdminActor, prova_id: int, request: UpdateProvaRequest
) -> Prova:
    now = context.clock.now()
    with context.database.transaction() as connection:
        row = catalog_repo.get_prova_row(connection, prova_id)
        if row is None:
            raise not_found("prova")
        before = catalog_repo.prova_from_row(row)
        changes: dict[str, Any] = {}
        what = []
        relabelled_jobs = 0
        if request.name is not None:
            name = _clean_name(request.name)
            if name != before.name:
                _check_name_is_free(connection, name, own_id=prova_id)
                changes["name"] = name
                # Job.prova holds the name: move existing jobs to the new one.
                relabelled_jobs = catalog_repo.rename_prova_on_jobs(connection, before.name, name)
                what.append(f"renamed '{before.name}' → '{name}' ({relabelled_jobs} job(s))")
        if request.active is not None and request.active != before.active:
            changes["active"] = int(request.active)
            what.append("activated" if request.active else "deactivated")
        if changes:
            catalog_repo.update_prova(connection, prova_id, now, changes)
        after = catalog_repo.prova_from_row(catalog_repo.get_prova_row(connection, prova_id))
        record_admin_change(
            connection,
            now,
            actor,
            "prova_updated",
            f"prova '{after.name}': {', '.join(what) or 'no change'}",
            before=_prova_json(before),
            after=_prova_json(after) | {"relabelled_jobs": relabelled_jobs},
        )
    return after


def reorder_provas(
    context: OrchestratorContext, actor: AdminActor, request: ReorderProvasRequest
) -> ProvasResponse:
    """Put the provas in the given order; the request must list every prova exactly once."""
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = provas_catalog.list_provas(connection)
        known_ids = sorted(prova.id for prova in before)
        if sorted(request.prova_ids) != known_ids:
            raise validation_error(
                "prova_ids must list every prova exactly once", expected=known_ids
            )
        for position, prova_id in enumerate(request.prova_ids, start=1):
            catalog_repo.update_prova(connection, prova_id, now, {"display_order": position})
        after = provas_catalog.list_provas(connection)
        record_admin_change(
            connection,
            now,
            actor,
            "provas_reordered",
            "new order: " + ", ".join(prova.name for prova in after),
            before=[prova.name for prova in before],
            after=[prova.name for prova in after],
        )
    return ProvasResponse(provas=after)
