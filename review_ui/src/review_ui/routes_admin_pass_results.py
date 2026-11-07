"""Configurações → "Resultados das passagens": save or test the configuration (LIVESTACK-47).

One plain HTML form with two buttons (name="action"):

* "Salvar" (save): PUT /admin/pass-results/config. Success goes back to
  the page with ?feito=; a refused configuration (422) shows the page
  again with the text as typed and where the problem is. The old
  configuration stays in force (the orchestrator only stores a valid one).
* "Testar" (test): POST /admin/pass-results/test with the text in the box,
  saved or not, and shows the line for one prova, car and passagem.
"""

from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from review_ui.admin_view import redirect_done, show_refusal
from review_ui.catalog import text
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError
from review_ui.pass_results_settings_view import (
    PassResultsForm,
    config_refusal,
    too_long_refusal,
    trial_outcome,
)
from review_ui.request_context import admin_client_for
from review_ui.routes_admin_operations import (
    SETTINGS_PATH,
    saved_pass_results,
    settings_page_response,
)
from shared.models import (
    MAX_PASS_RESULTS_CONFIG_LENGTH,
    PassResultTestRequest,
    utc_now,
)

router = APIRouter(prefix="/review/admin")


def typed_config_text(form: dict[str, str]) -> str:
    """The textarea as typed. Browsers send line ends as CRLF; stored with plain "\\n"."""
    return form.get("config_text", "").replace("\r\n", "\n")


@router.post("/pass-results")
def pass_results_form(request: Request, form: dict = Depends(read_form)) -> Response:
    config_text = typed_config_text(form)
    typed = PassResultsForm(
        config_text=config_text,
        prova=form.get("test_prova", "").strip(),
        car=form.get("test_car", "").strip(),
        passagem=form.get("test_passagem", "").strip(),
    )
    if len(config_text) > MAX_PASS_RESULTS_CONFIG_LENGTH:
        refusal = too_long_refusal(MAX_PASS_RESULTS_CONFIG_LENGTH)
        return _page_again(request, typed, refusal, 422)
    if form.get("action") == "test":
        return _test(request, typed)
    return _save(request, typed)


def _page_again(request: Request, typed: PassResultsForm, error, status_code: int) -> Response:
    """The Settings page with the block as typed and its message."""
    shown = replace(typed, error=error)
    return settings_page_response(request, status_code=status_code, pass_results_form=shown)


def _save(request: Request, typed: PassResultsForm) -> Response:
    try:
        admin_client_for(request).update_pass_results_config(typed.config_text or "")
    except OrchestratorError as error:
        if error.status_code == 422 and error.details.get("problem"):
            return _page_again(request, typed, config_refusal(error, for_test=False), 422)
        return show_refusal(
            error, lambda reason, status: _page_again(request, typed, reason, status)
        )
    return redirect_done(SETTINGS_PATH, "pass_results_saved")


def _test(request: Request, typed: PassResultsForm) -> Response:
    try:
        body = PassResultTestRequest(
            prova_id=typed.prova,
            car_number=int(typed.car),
            passagem=int(typed.passagem),
            # The text in the box, so an edit is tried before it is saved.
            config_text=typed.config_text,
        )
    except ValueError:
        return _page_again(request, typed, text("pass_results.test.missing_fields"), 400)
    admin = admin_client_for(request)
    try:
        answer = admin.run_pass_result_test(body)
    except OrchestratorError as error:
        if error.status_code == 422 and error.details.get("problem"):
            return _page_again(request, typed, config_refusal(error, for_test=True), 422)
        return show_refusal(
            error, lambda reason, status: _page_again(request, typed, reason, status)
        )
    saved = saved_pass_results(admin)
    unsaved = saved is None or saved.config_text != typed.config_text
    outcome = trial_outcome(body, answer, utc_now(), unsaved=unsaved)
    shown = replace(typed, outcome=outcome)
    return settings_page_response(request, pass_results_form=shown)
