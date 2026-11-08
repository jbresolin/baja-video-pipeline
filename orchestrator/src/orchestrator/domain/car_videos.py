"""GET /review/car-videos: one car's videos in one prova (LIVESTACK-45).

The job page shows them beside the passagem field, so the reviewer can see
which passes of that car are already numbered and where this video falls
in filming order. The review UI works out the suggestion and the warnings
from this list (review_ui/passagem_help_view.py).

Jobs are matched by the prova's clip folder (its slug), not by its name:
a slug is fixed when the prova is created (domain/provas_catalog.py), so
a renamed prova, or a page that still shows the old name, finds the same
videos.
"""

import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.domain import provas_catalog, sender_messages
from orchestrator.domain.job_view import job_from_row
from orchestrator.domain.review_queue import summaries_with_message_counts
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import jobs as jobs_repo
from shared.models import CarVideosResponse


def prova_slug_for(connection: sqlite3.Connection, prova: str) -> str | None:
    """The clip folder of a prova given by name or by slug; None if it has none.

    A name in the provas table gives its stored slug; else a slug in the
    table is taken as it is; else the slug of the text itself (a prova
    typed before the table existed, or an old name after a rename, whose
    slug is still the folder's).
    """
    prova = prova.strip()
    if catalog_repo.find_prova_row_by_name(connection, prova) is not None:
        return provas_catalog.clip_folder_for(connection, prova)
    by_slug = catalog_repo.find_prova_row_by_slug(connection, prova)
    if by_slug is not None:
        return by_slug["slug"]
    try:
        return provas_catalog.clip_folder_for(connection, prova)
    except ValueError:
        # No letters or digits: no folder, so no job can be in it.
        return None


def car_videos(context: OrchestratorContext, prova: str, car_number: int) -> CarVideosResponse:
    with context.database.transaction() as connection:
        slug = prova_slug_for(connection, prova)
        rows = []
        if slug is not None:
            folder_of = {}
            for row in jobs_repo.list_car_job_rows(connection, car_number):
                if row["prova"] not in folder_of:
                    folder_of[row["prova"]] = prova_slug_for(connection, row["prova"])
                if folder_of[row["prova"]] == slug:
                    rows.append(row)
        jobs = [job_from_row(connection, row, context.layout, context.clock.now()) for row in rows]
        counts = sender_messages.message_counts(connection, rows)
    return CarVideosResponse(
        prova_slug=slug,
        car_number=car_number,
        items=summaries_with_message_counts(jobs, counts),
    )
