"""The orchestrator API contract, written as FastAPI route *stubs*.

Every route the orchestrator must offer is declared here with its exact
path, method, auth, request and response models (all from shared.models),
but with no logic: each handler only raises NotImplementedError.

FastAPI turns these declarations into an OpenAPI document, which
tools/generate_openapi.py writes to orchestrator/openapi.yaml. Because the
YAML is generated from the same pydantic models the services use, the
contract cannot drift from the code. tests/tools checks the committed YAML
is up to date.

The real orchestrator (LIVESTACK-24) does NOT import this package; it
implements the same routes itself and can compare its own app.openapi()
paths against build_contract_app().
"""
