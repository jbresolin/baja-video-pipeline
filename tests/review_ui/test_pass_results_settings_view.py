"""The Settings page's "Resultados das passagens" in words, without a server (LIVESTACK-47).

* A configuration the orchestrator refuses (422 with details {path,
  problem, ...}) becomes one precise pt-BR sentence per problem code.
* "Testar" offers the provas of the text being edited, and names penalty
  icons the configuration has no name for.
* Each configured prova says whether its table is fetched, and why not.
"""

import pytest

from review_ui.orchestrator_client import OrchestratorError
from review_ui.pass_results_settings_view import (
    config_refusal,
    prova_status_row,
    trial_outcome,
    trial_prova_choices,
)
from shared.models import (
    PassResultCell,
    PassResultFetch,
    PassResultsProvaStatus,
    PassResultTestRequest,
    PassResultTestResponse,
)
from tests.review_ui.sample_data import T0


def refusal(**details) -> str:
    error = OrchestratorError(422, "validation_error", "an English message", details)
    return str(config_refusal(error, for_test=False))


@pytest.mark.parametrize(
    ("details", "words"),
    [
        ({"problem": "not_json", "line": 3, "column": 5}, "não é JSON válido (linha 3, coluna 5)"),
        (
            {"problem": "wrong_type", "expected": "boolean"},
            "<code>true</code> ou <code>false</code>",
        ),
        ({"problem": "wrong_type", "expected": "text"}, "o valor deve ser um texto entre aspas"),
        ({"problem": "wrong_type", "expected": "object"}, "um objeto (entre chaves)"),
        ({"problem": "wrong_type", "expected": "whole_number"}, "um número inteiro"),
        ({"problem": "wrong_type", "expected": "list"}, "uma lista (entre colchetes)"),
        ({"problem": "too_many", "maximum": 4}, "no máximo 4 itens"),
        ({"problem": "unknown_key", "allowed": ["column", "unit"]}, "(aceitas: column, unit)"),
        ({"problem": "missing_key"}, "falta esta chave, que é obrigatória"),
        ({"problem": "empty_text"}, "o texto não pode ficar vazio"),
        ({"problem": "too_long", "max_length": 40}, "no máximo 40 caracteres"),
        ({"problem": "out_of_range", "minimum": 0, "maximum": 3}, "entre 0 e 3"),
        ({"problem": "not_one_of", "choices": ["list_columns", "rows"]}, "list_columns, rows"),
        ({"problem": "bad_id"}, "de 1 a 3 letras ou números"),
        ({"problem": "bad_name"}, "o nome do ícone só pode ter"),
        ({"problem": "nothing_to_show"}, "<code>result</code> ou <code>clock</code>"),
    ],
)
def test_every_problem_code_has_a_precise_sentence(details, words):
    message = refusal(path="provas.MAN.x", **details)

    assert message.startswith("Configuração não salva: em <code>provas.MAN.x</code>, ")
    assert words in message
    # Known codes need no English after them.
    assert "an English message" not in message


def test_an_unknown_problem_code_falls_back_with_the_english_message():
    message = refusal(path="provas", problem="something_new")

    assert "a configuração não é válida" in message
    assert '<span lang="en">an English message</span>' in message


def test_a_path_with_html_in_it_is_escaped():
    # The path holds keys the admin typed: it must stay text.
    message = refusal(path="provas.<b>", problem="bad_id")
    assert "<code>provas.&lt;b&gt;</code>" in message


def test_testar_offers_the_provas_of_the_text_being_edited():
    edited = '{"provas": {"MAN": {}, "END": {}, "_comment": "x"}}'
    assert trial_prova_choices(edited, None) == ["MAN", "END"]
    # Text that does not parse: nothing to choose (a plain field is shown).
    assert trial_prova_choices("{", None) == []


def test_testar_names_penalty_icons_the_configuration_has_no_name_for():
    # Setup: the API drew a "flag" icon the configuration does not name.
    request = PassResultTestRequest(prova_id="MAN", car_number=2, passagem=1)
    answer = PassResultTestResponse(
        text="58 s · 1 flag → 60 s",
        unknown_penalties=["flag"],
        cells=[
            [PassResultCell(column="Tempo", raw=["58 (1 )"], display="58 (1<img class='flag'>)")]
        ],
    )

    outcome = trial_outcome(request, answer, T0, unsaved=False)

    assert outcome.unknown_penalties == "Ícones de penalidade sem nome na configuração: flag"
    # The cells become plain strings (a list of runs joined); the template escapes them.
    assert outcome.rows == [[("Tempo", "58 (1 )", "58 (1<img class='flag'>)")]]


@pytest.mark.parametrize(
    ("polling", "event_finished", "words"),
    [
        (True, False, "buscando a cada 30 s"),
        (False, False, "parada: nenhum vídeo desta prova precisa agora"),
        (False, True, "parada: o evento terminou e a última busca foi feita"),
    ],
)
def test_each_prova_says_whether_its_table_is_fetched(polling, event_finished, words):
    prova = PassResultsProvaStatus(
        prova_id="MAN",
        prova_name="Manobrabilidade",
        resultado_id="26BR_MAN",
        polling=polling,
        fetch=PassResultFetch(
            resultado_id="26BR_MAN",
            last_attempt_at=T0,
            last_attempt_ok=True,
            last_success_at=T0,
            event_finished=event_finished,
        ),
    )
    assert prova_status_row(prova, T0).schedule == words
