"""Fetching the configured provas' results tables (LIVESTACK-47).

Which tables: those of configured provas that have jobs whose result
matters (in review, ready, rendering, uploaded or delivered, however long
ago; repositories/pass_results.py), so a correction made the next morning
still flags a delivered clip. When: every 30 s, or less often when the API
says its answer stays fresh longer. The API caches 5 s and allows 120
requests a minute per endpoint; a handful of provas at 2 requests a minute
each stays far below that.

Until the event is finished. The API says so by caching its answers 300 s
instead of 5 (Cache-Control max-age). After the first answer that says it,
one last fetch follows (300 s later, so a fresh answer), and then that
table is no longer fetched. An answer saying the event runs again (only
"Testar" could still see one) starts fetching again.

Also on demand, from "Testar" on the Settings page (reusing an answer
from the last 5 seconds, which the API would serve from its cache anyway),
and from the job page's pass preview (LIVESTACK-53) when the prova's table
was never fetched: the first video of a prova would otherwise show "ainda
não buscado" until it was saved. That fetch waits at most 3 s, and is not
tried again within 30 s of the table's last try (by anyone), so a reviewer
typing never hammers the API; when it fails the preview says "ainda não
buscado", as before.

A failure never removes the last good answer: texts keep coming from it,
marked stale, so nothing on air is blanked because the API had a bad
minute. Each table records when it was last fetched and whether it worked.
"""

import json
import logging
from datetime import datetime, timedelta

from orchestrator.clients.results_api import ResultsApiError
from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.pass_results import (
    api_prova_id,
    current_config,
    event_finished,
    fetching_stopped,
    resultado_id_for,
    stored_table,
)
from orchestrator.domain.results_table import BadTableError, parse_table
from orchestrator.repositories import pass_results as pass_results_repo

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=30)
# "Testar" reuses an answer at most this old.
TEST_REUSE = timedelta(seconds=5)
# The pass preview's fetch of a table never fetched: how long it may wait
# for the API, and how soon after the table's last try it may try again.
PREVIEW_FETCH_TIMEOUT_S = 3.0
PREVIEW_FETCH_INTERVAL = POLL_INTERVAL


def tables_needing_results(context: OrchestratorContext, now: datetime) -> list[str]:
    """Resultado ids ("26BR_MAN") of configured provas whose jobs need results.

    Tables whose fetching stopped (the event is finished) are left out.
    """
    event = context.admin_settings.current.event_id
    if not event:
        return []
    with context.database.transaction() as connection:
        config = current_config(connection)
        if not config.provas:
            return []
        names = pass_results_repo.prova_names_with_jobs_needing_results(connection)
        wanted = set()
        for name in names:
            prova_config = config.for_prova(api_prova_id(connection, name))
            if prova_config is not None:
                wanted.add(resultado_id_for(event, prova_config))
        wanted = {
            resultado_id
            for resultado_id in wanted
            if not fetching_stopped(pass_results_repo.get_table_row(connection, resultado_id))
        }
    return sorted(wanted)


def is_due(row, now: datetime) -> bool:
    """Never tried, or the last try is 30 s old (or older than the answer's max-age)."""
    if row is None or row["attempt_at"] is None:
        return True
    wait = POLL_INTERVAL
    if row["attempt_ok"] and row["max_age_s"]:
        wait = max(wait, timedelta(seconds=row["max_age_s"]))
    return now - from_db_time(row["attempt_at"]) >= wait


def fetch_table(
    context: OrchestratorContext, resultado_id: str, timeout_s: float | None = None
) -> bool:
    """Ask the API for one table now and record the outcome; True when it worked.

    The request runs outside any transaction (it may take seconds; at most
    `timeout_s` per wait when given).
    """
    settings = context.admin_settings.current
    try:
        answer = context.results_api.results_table(
            settings.results_api_base_url, settings.event_id, resultado_id, timeout_s
        )
        parse_table(answer.body)  # refuse an answer we could not read later
    except (ResultsApiError, BadTableError) as error:
        logger.warning("results table %s not fetched: %s", resultado_id, error)
        with context.database.transaction() as connection:
            pass_results_repo.record_failure(
                connection, resultado_id, str(error), context.clock.now()
            )
        return False
    with context.database.transaction() as connection:
        pass_results_repo.record_success(
            connection,
            resultado_id,
            json.dumps(answer.body, ensure_ascii=False),
            answer.max_age_s,
            event_finished(answer.max_age_s),
            context.clock.now(),
        )
    return True


def fetch_if_older_than(context: OrchestratorContext, resultado_id: str, age: timedelta) -> None:
    """For "Testar": fetch unless the last try was within `age`."""
    with context.database.transaction() as connection:
        row = pass_results_repo.get_table_row(connection, resultado_id)
    now = context.clock.now()
    if row is None or row["attempt_at"] is None or now - from_db_time(row["attempt_at"]) >= age:
        fetch_table(context, resultado_id)


def fetch_if_never_fetched(context: OrchestratorContext, resultado_id: str) -> None:
    """For the pass preview: fetch a table that has no answer yet, at most once per 30 s.

    One preview at a time per table: a second one waits for the first's
    fetch and then finds the answer, or the failed try it must not repeat.
    """
    with context.table_fetch_locks.lock_for(resultado_id):
        with context.database.transaction() as connection:
            row = pass_results_repo.get_table_row(connection, resultado_id)
        if stored_table(row) is not None or _tried_lately(context, row):
            return
        fetch_table(context, resultado_id, PREVIEW_FETCH_TIMEOUT_S)


def _tried_lately(context: OrchestratorContext, row) -> bool:
    """The table's last try (by anyone) was less than 30 s ago."""
    if row is None or row["attempt_at"] is None:
        return False
    return context.clock.now() - from_db_time(row["attempt_at"]) < PREVIEW_FETCH_INTERVAL


def poll_due_tables(context: OrchestratorContext) -> list[str]:
    """The background loop's pass: fetch every needed table that is due. Returns those fetched."""
    now = context.clock.now()
    fetched = []
    for resultado_id in tables_needing_results(context, now):
        with context.database.transaction() as connection:
            row = pass_results_repo.get_table_row(connection, resultado_id)
        if is_due(row, now):
            fetch_table(context, resultado_id)
            fetched.append(resultado_id)
    return fetched
