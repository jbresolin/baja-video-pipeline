"""The one module that lists provas (admin page brief: "leave a seam").

The list is the `provas` table: provas synced from the Baja results API
(domain/results_sync.py) plus manual ones from the admin Provas page.
Everything else (the review form's select, review validation, clip
folder names) asks these functions and nothing else.

Clip folder rule: a prova's slug is fixed when it is created
(shared.layout.slugify_prova of its first name). Renaming a prova changes
its name, relabels every job that had the old name, and keeps the slug,
so clips keep landing in the same folder on the studio box.
"""

import sqlite3

from orchestrator.repositories import catalog as catalog_repo
from shared.layout import slugify_prova
from shared.models import Prova


def list_provas(connection: sqlite3.Connection) -> list[Prova]:
    """Every prova in display order, inactive ones included."""
    return [catalog_repo.prova_from_row(row) for row in catalog_repo.list_prova_rows(connection)]


def is_known_prova(connection: sqlite3.Connection, name: str) -> bool:
    """True if a job may be made ready with this prova (active or not).

    An inactive prova stays valid, so a job reviewed before it was
    deactivated can still be reprocessed without picking a new prova.
    """
    return catalog_repo.find_prova_row_by_name(connection, name) is not None


def clip_folder_for(connection: sqlite3.Connection, prova_name: str) -> str:
    """The clip folder (slug) for a prova name.

    The stored slug when the prova is in the table; else the slug of the
    name itself (jobs made before the provas table existed).
    """
    row = catalog_repo.find_prova_row_by_name(connection, prova_name)
    return row["slug"] if row is not None else slugify_prova(prova_name)
