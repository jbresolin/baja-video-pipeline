"""The Settings page's pass-results block (LIVESTACK-47).

    GET  /admin/pass-results         the configuration and each prova's fetch status
    PUT  /admin/pass-results/config  replace the configuration (checked first)
    POST /admin/pass-results/test    "Testar": the text for one prova, car and passagem

A configuration that does not pass parse_config is refused with 422 and
the path of the bad key; the one in force stays. "Testar" fetches the
prova's table now and may try a configuration that is not saved yet.
"""

from orchestrator.context import OrchestratorContext
from orchestrator.domain import pass_results_fetch
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.domain.pass_clock import official_clock
from orchestrator.domain.pass_results import (
    CONFIG_KEY,
    config_text,
    fetch_status,
    parsed_config,
    resultado_id_for,
    stored_table,
)
from orchestrator.domain.pass_results_config import ConfigError, parse_config
from orchestrator.domain.pass_text import PassText, build_pass_text
from orchestrator.domain.results_table import ResultsTable
from orchestrator.errors import ApiError
from orchestrator.repositories import app_settings as settings_repo
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import pass_results as pass_results_repo
from shared.models import (
    ErrorCode,
    PassResultCell,
    PassResultProblem,
    PassResultsProvaStatus,
    PassResultsSettings,
    PassResultTestRequest,
    PassResultTestResponse,
    UpdatePassResultsConfigRequest,
)


def _refuse(error: ConfigError) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, str(error), error.details())


def pass_results_settings(context: OrchestratorContext) -> PassResultsSettings:
    now = context.clock.now()
    polled = set(pass_results_fetch.tables_needing_results(context, now))
    event = context.admin_settings.current.event_id
    with context.database.transaction() as connection:
        text = config_text(connection)
        provas = []
        for prova_id, prova_config in parsed_config(text).provas.items():
            synced = catalog_repo.find_synced_prova_row(connection, prova_id)
            resultado_id = resultado_id_for(event, prova_config) if event else None
            row = pass_results_repo.get_table_row(connection, resultado_id) if event else None
            provas.append(
                PassResultsProvaStatus(
                    prova_id=prova_id,
                    prova_name=synced["name"] if synced is not None else None,
                    resultado_id=resultado_id,
                    polling=resultado_id in polled,
                    fetch=fetch_status(row, resultado_id) if resultado_id else None,
                )
            )
    return PassResultsSettings(config_text=text, provas=provas)


def update_config(
    context: OrchestratorContext, actor: AdminActor, request: UpdatePassResultsConfigRequest
) -> PassResultsSettings:
    """Check, then store the text as typed (with one audit event); refuse a bad one."""
    try:
        parse_config(request.config_text)
    except ConfigError as error:
        raise _refuse(error) from error
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = config_text(connection)
        if before != request.config_text:
            settings_repo.save_setting(connection, CONFIG_KEY, request.config_text, now)
            record_admin_change(
                connection,
                now,
                actor,
                "pass_results_config_updated",
                "pass-results configuration changed",
                before={"config_text": before},
                after={"config_text": request.config_text},
            )
    return pass_results_settings(context)


def test_pass_result(
    context: OrchestratorContext, request: PassResultTestRequest
) -> PassResultTestResponse:
    """Fetch the prova's table (unless fresh) and build the text, as a render would."""
    if request.config_text is None:
        with context.database.transaction() as connection:
            config = parsed_config(config_text(connection))
    else:
        try:
            config = parse_config(request.config_text)
        except ConfigError as error:
            raise _refuse(error) from error
    prova_config = config.for_prova(request.prova_id)
    if prova_config is None:
        return PassResultTestResponse(
            problem=PassResultProblem.NOT_CONFIGURED,
            problem_detail=f"no prova '{request.prova_id}' in the configuration",
        )
    event = context.admin_settings.current.event_id
    if not event:
        return PassResultTestResponse(problem=PassResultProblem.NO_EVENT)
    resultado_id = resultado_id_for(event, prova_config)
    pass_results_fetch.fetch_if_older_than(context, resultado_id, pass_results_fetch.TEST_REUSE)
    with context.database.transaction() as connection:
        row = pass_results_repo.get_table_row(connection, resultado_id)
    table = stored_table(row)
    answer = PassResultTestResponse(
        resultado_id=resultado_id, fetch=fetch_status(row, resultado_id)
    )
    if table is None:
        return answer.model_copy(update={"problem": PassResultProblem.NOT_FETCHED})
    outcome = build_pass_text(
        table, prova_config, config.penalties, request.car_number, request.passagem
    )
    if prova_config.result is None:
        # A prova with only a clock: no line, and no problem to report for it.
        outcome = PassText()
    clock = official_clock(table, prova_config, request.car_number, request.passagem)
    return answer.model_copy(
        update={
            "text": outcome.text,
            "problem": outcome.problem,
            "problem_detail": outcome.detail,
            "cells": _car_cells(table, request.car_number),
            "unknown_penalties": list(outcome.unknown_penalties),
            # The running clock's stop (LIVESTACK-48), when the prova has a clock.
            "clock_configured": prova_config.clock is not None,
            "clock_text": clock.text,
            "clock_problem": None if prova_config.clock is None else clock.problem,
            "clock_problem_detail": None if prova_config.clock is None else clock.detail,
        }
    )


def _car_cells(table: ResultsTable, car_number: int) -> list[list[PassResultCell]]:
    """The car's rows as the API sent them, cell by cell with their column."""
    rows = []
    for row in table.rows_of_car(car_number):
        cells = row.get("cells") if isinstance(row.get("cells"), list) else []
        rows.append(
            [
                PassResultCell(
                    column=table.columns[index] if index < len(table.columns) else None,
                    raw=cell.get("raw") if isinstance(cell, dict) else None,
                    display=cell.get("display") if isinstance(cell, dict) else None,
                )
                for index, cell in enumerate(cells)
            ]
        )
    return rows
