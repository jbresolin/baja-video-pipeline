"""Every API model says which way it travels (design §14).

ReceivedByServer: what the orchestrator receives; unknown fields refused.
ReadByClients: what a worker, the studio or the review UI reads; unknown
fields ignored, so a newer orchestrator never breaks an older client.

This test fails when a new model in shared.models skips that choice, or
when its docstring does not say which one it made.
"""

import inspect

import pytest
from pydantic import BaseModel, ValidationError

import shared.models
from shared.models import ClaimRequest, JobStateResponse
from shared.models.common import ReadByClients, ReceivedByServer
from shared.models.evolution import EvolutionModel


def api_models() -> list[type[BaseModel]]:
    """Every model exported by shared.models, except Evolution's webhook
    shapes (the orchestrator reads those from Evolution, a third party)."""
    found = []
    for _, value in inspect.getmembers(shared.models, inspect.isclass):
        if issubclass(value, BaseModel) and not issubclass(value, EvolutionModel):
            found.append(value)
    return found


def test_every_api_model_picks_exactly_one_direction():
    for model in api_models():
        directions = [issubclass(model, ReceivedByServer), issubclass(model, ReadByClients)]
        assert directions.count(True) == 1, model.__name__


def test_each_docstring_says_which_direction():
    for model in api_models():
        expected = (
            "Received by the orchestrator: unknown fields are refused."
            if issubclass(model, ReceivedByServer)
            else "Read by clients: unknown fields are ignored."
        )
        assert expected in " ".join((model.__doc__ or "").split()), model.__name__


def test_the_two_bases_refuse_and_ignore():
    # The rule itself, on two real models: a request with a typo is refused...
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        ClaimRequest.model_validate({"task_kind": "process", "task_knd": "process"})
    # ...and an answer with a field this client does not know is read anyway.
    answer = JobStateResponse.model_validate({"job_id": "j1", "state": "done", "new": 1})
    assert answer.job_id == "j1"
