"""The results API client (orchestrator/clients/results_api.py) against the fake.

The fake (orchestrator/testing/fake_results_api.py) answers with the
example answers copied from the API's own documentation, and refuses
requests the way the documentation says the real API does. So these tests
check that the client asks exactly what the API accepts and reads exactly
what it answers.
"""

import httpx
import pytest

from orchestrator.clients.results_api import ResultsApiClient, ResultsApiError, clean_name
from orchestrator.testing.fake_results_api import FAKE_RESULTS_API_URL, FakeResultsApi


@pytest.fixture
def fake() -> FakeResultsApi:
    return FakeResultsApi()


@pytest.fixture
def client(fake) -> ResultsApiClient:
    return ResultsApiClient(httpx.Client(transport=fake.transport()))


def test_the_events_provas_are_read_in_the_apis_order(client, fake):
    provas = client.event_provas(FAKE_RESULTS_API_URL, "26BR")
    assert [(p.prova_id, p.name, p.type) for p in provas] == [
        ("SGE", "Segurança Estática", "normal"),
        ("END", "Enduro", "laps"),
        ("EPN", "Penalizações Enduro", "rolling"),
    ]
    # Exactly one parameter, evt, as typed: the API refuses anything else.
    assert fake.requests == [("/resultados/evento.php", "evt=26BR")]


def test_the_events_teams_are_read_by_car_number(client):
    teams = client.event_teams(FAKE_RESULTS_API_URL, "26BR")
    assert [(t.car_number, t.name, t.short_name) for t in teams] == [
        (1, "BAJA UFMG", "Baja UFMG"),
        (2, "Equipe FEI Baja", "FEI"),
    ]
    assert teams[1].school == "Fundação Educacional Inaciana - FEI"


def test_a_wrong_event_id_is_not_found_and_case_matters(client):
    # "26br" is not "26BR" (ids are written exactly as stored).
    with pytest.raises(ResultsApiError) as caught:
        client.event_provas(FAKE_RESULTS_API_URL, "26br")
    assert caught.value.code == "not_found"
    assert "26br not found" in str(caught.value)


def test_a_rate_limit_says_how_long_to_wait(client, fake):
    # The doc's 429: "Tente novamente em 12 segundos", Retry-After: 12.
    fake.rate_limited_for_s = 12
    with pytest.raises(ResultsApiError) as caught:
        client.event_teams(FAKE_RESULTS_API_URL, "26BR")
    assert (caught.value.code, caught.value.retry_after_s) == ("rate_limited", 12)


def test_an_unreachable_api_is_an_error_not_a_crash():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    client = ResultsApiClient(httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(ResultsApiError) as caught:
        client.event_provas(FAKE_RESULTS_API_URL, "26BR")
    assert caught.value.code == "unreachable"


def test_a_malformed_event_id_is_refused_before_any_request(client, fake):
    with pytest.raises(ResultsApiError):
        client.event_provas(FAKE_RESULTS_API_URL, "26-BR")
    assert fake.requests == []


def test_names_are_cleaned_of_trailing_spaces_and_html_entities():
    # The doc warns names are "as stored", e.g. "Procobaja " from 25SU.
    assert clean_name("Procobaja ") == "Procobaja"
    assert clean_name("Tra&ccedil;&atilde;o  e   Lama") == "Tração e Lama"
    assert clean_name("   ") is None
    assert clean_name(None) is None


def test_unknown_fields_and_bad_entries_are_ignored(client, fake):
    # The contract only adds fields; entries without a number or name are skipped.
    fake.teams_of().append({"number": "x", "name": "No number"})
    fake.teams_of().append({"number": 9, "name": "Nove", "brand_new_field": 1})
    teams = client.event_teams(FAKE_RESULTS_API_URL, "26BR")
    assert [t.car_number for t in teams] == [1, 2, 9]
