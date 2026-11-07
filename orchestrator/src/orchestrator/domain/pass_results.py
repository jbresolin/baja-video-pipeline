"""A job's official pass result, from the latest results fetched (LIVESTACK-47).

    configuration (Settings)  ─┐
    the job's prova, car, passagem ─┼─► pass_text.build_pass_text ─► "66 s · 4 cones → 86 s"
    last good answer of its table ─┘

The job page (Job.pass_result), the render task
(ProcessTask.pass_result_text), the render features it needs and the
"clip no longer matches" check (render_inputs.inputs_now) all come
through here, so they always see the same text.

A job's prova is matched to the configuration by its results API id
(Prova.external_id, set by the sync from the results API); a manual prova
has none and so never gets a result line. The event is the Settings'
event id, so the configuration's "MAN" means table "26BR_MAN" in 2026.
"""

import json
import logging
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from orchestrator.db.database import from_db_time
from orchestrator.domain.pass_results_config import (
    EMPTY_CONFIG,
    ConfigError,
    PassResultsConfig,
    ProvaPassConfig,
    parse_config,
)
from orchestrator.domain.pass_text import PassText, build_pass_text
from orchestrator.domain.results_table import BadTableError, ResultsTable, parse_table
from orchestrator.repositories import app_settings as settings_repo
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import pass_results as pass_results_repo
from shared.layout import slugify_prova
from shared.models import CatalogSource, PassResult, PassResultFetch, PassResultProblem

logger = logging.getLogger(__name__)

CONFIG_KEY = "pass_results_config"
# Parsed configurations and tables, so a queue of 100 jobs parses each once.
# Both are cached by their text: a new answer is a new key. Request
# threads and the background loop share them, hence the lock.
_CACHE_SIZE = 32
_cache_lock = threading.Lock()
_parsed_configs: OrderedDict[str, PassResultsConfig] = OrderedDict()
_parsed_tables: OrderedDict[str, ResultsTable | None] = OrderedDict()


def _cached(cache: OrderedDict, key: str, parse: Callable[[str], Any]) -> Any:
    """The parsed value for `key`, parsing (outside the lock) when it is not cached."""
    with _cache_lock:
        if key in cache:
            return cache[key]
    value = parse(key)
    with _cache_lock:
        cache[key] = value
        while len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
    return value


# --- The configuration ---


def config_text(connection: sqlite3.Connection) -> str:
    """The configuration as the admin saved it; "" when never saved."""
    return settings_repo.load_settings_values(connection).get(CONFIG_KEY) or ""


def parsed_config(text: str) -> PassResultsConfig:
    """`text` parsed (cached). A stored text was checked on save, so an error here
    means it was edited behind the orchestrator's back: logged, nothing configured."""
    return _cached(_parsed_configs, text, _parse_stored_config)


def _parse_stored_config(text: str) -> PassResultsConfig:
    try:
        return parse_config(text)
    except ConfigError as error:
        logger.error("stored pass-results configuration is invalid: %s", error)
        return EMPTY_CONFIG


def current_config(connection: sqlite3.Connection) -> PassResultsConfig:
    return parsed_config(config_text(connection))


def event_id(connection: sqlite3.Connection) -> str:
    return settings_repo.load_settings_values(connection).get("event_id") or ""


def resultado_id_for(event: str, prova_config: ProvaPassConfig) -> str:
    """ "26BR" + "MAN" → "26BR_MAN" (the results API's resultado id)."""
    return f"{event}_{prova_config.resultado}"


def api_prova_id(connection: sqlite3.Connection, prova_name: str | None) -> str | None:
    """The results API id of the prova a job names ("Manobrabilidade" → "MAN"), if synced."""
    if not prova_name:
        return None
    row = catalog_repo.find_prova_row_by_name(connection, prova_name)
    if row is None:
        try:
            row = catalog_repo.find_prova_row_by_slug(connection, slugify_prova(prova_name))
        except ValueError:
            return None
    if row is None or row["source"] != CatalogSource.RESULTS_API:
        return None
    return row["external_id"]


# --- The fetched tables ---


def fetch_status(row: sqlite3.Row | None, resultado_id: str) -> PassResultFetch:
    """How fetching a table has gone, from its pass_result_tables row (None: never tried)."""
    if row is None:
        return PassResultFetch(resultado_id=resultado_id)
    ok = None if row["attempt_ok"] is None else bool(row["attempt_ok"])
    return PassResultFetch(
        resultado_id=resultado_id,
        last_attempt_at=from_db_time(row["attempt_at"]),
        last_attempt_ok=ok,
        last_error=row["error"],
        last_success_at=from_db_time(row["success_at"]),
        failing_since=from_db_time(row["failing_since"]),
        stale=ok is False and row["body_json"] is not None,
    )


def stored_table(row: sqlite3.Row | None) -> ResultsTable | None:
    """The last good answer of a table, parsed (cached); None if there is none."""
    if row is None or row["body_json"] is None:
        return None
    return _cached(_parsed_tables, row["body_json"], _parse_stored_table)


def _parse_stored_table(body_json: str) -> ResultsTable | None:
    # fetch_table only stores answers parse_table accepted; this is a safety net.
    try:
        return parse_table(json.loads(body_json))
    except (ValueError, BadTableError) as error:
        logger.error("a stored results table is unreadable: %s", error)
        return None


# --- One job ---


def pass_result_for_job(connection: sqlite3.Connection, job_row: Any) -> PassResult:
    """Job.pass_result: the line the overlay would show now, the toggle, or why there is none.

    `job_row` is a jobs row, or a dict of its columns (a reprocess checks
    the fields it is about to save before they are stored).
    """
    config = current_config(connection)
    prova_config = config.for_prova(api_prova_id(connection, job_row["prova"]))
    if prova_config is None or prova_config.result is None:
        return PassResult(problem=PassResultProblem.NOT_CONFIGURED)
    chosen = job_row["show_pass_result"]
    show = prova_config.show_by_default if chosen is None else bool(chosen)
    event = event_id(connection)
    if not event:
        return PassResult(configured=True, show=show, problem=PassResultProblem.NO_EVENT)
    resultado_id = resultado_id_for(event, prova_config)
    table_row = pass_results_repo.get_table_row(connection, resultado_id)
    fetch = fetch_status(table_row, resultado_id)
    outcome = _text_for(
        stored_table(table_row),
        prova_config,
        config,
        job_row["car_number"],
        job_row["passagem"],
    )
    return PassResult(
        configured=True,
        show=show,
        text=outcome.text,
        problem=outcome.problem,
        problem_detail=outcome.detail,
        fetch=fetch,
    )


def _text_for(
    table: ResultsTable | None,
    prova_config: ProvaPassConfig,
    config: PassResultsConfig,
    car_number: int | None,
    passagem: int | None,
) -> PassText:
    if car_number is None or passagem is None:
        return PassText(problem=PassResultProblem.INCOMPLETE_REVIEW)
    if table is None:
        return PassText(problem=PassResultProblem.NOT_FETCHED)
    return build_pass_text(table, prova_config, config.penalties, car_number, passagem)


def render_text(result: PassResult) -> str | None:
    """What a render burns in: the text when the toggle shows it, else nothing."""
    return result.text if result.show else None


def pass_text_for_render(connection: sqlite3.Connection, job_row: Any) -> str | None:
    """ProcessTask.pass_result_text for this job now (None = no result pill)."""
    return render_text(pass_result_for_job(connection, job_row))
