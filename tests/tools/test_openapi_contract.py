"""Tests for the API contract (tools/openapi_contract → orchestrator/openapi.yaml).

The other agents build against orchestrator/openapi.yaml. These tests make
sure that file (1) is exactly what the contract code generates, so nobody
edited it by hand or forgot to regenerate it, (2) contains every endpoint
the design asks for, and (3) protects every endpoint with some kind of auth
except the two that must be open.
"""

import yaml

from tools.generate_openapi import OPENAPI_PATH, render_openapi_yaml

# Every (method, path) the orchestrator must offer. Written out by hand
# from the work item, so a route deleted by mistake shows up here.
REQUIRED_ENDPOINTS = {
    ("get", "/health"),
    # Intake
    ("post", "/intake/evolution-webhook"),
    ("post", "/intake/test-upload"),
    # Enrollment
    ("post", "/enroll"),
    ("post", "/admin/enrollment-tokens"),
    # Worker loop
    ("post", "/workers/{worker_id}/heartbeat"),
    ("post", "/workers/{worker_id}/next"),
    ("get", "/workers"),
    ("post", "/jobs/{job_id}/claim"),
    ("post", "/jobs/{job_id}/lease/renew"),
    ("post", "/jobs/{job_id}/failed"),
    ("get", "/auth/file"),
    ("post", "/jobs/{job_id}/holder-confirm"),
    ("post", "/jobs/{job_id}/proxy"),
    ("put", "/jobs/{job_id}/thumbnail"),
    ("post", "/jobs/{job_id}/clip"),
    ("post", "/jobs/{job_id}/delivered"),
    # Review
    ("get", "/review/queue"),
    ("get", "/jobs/{job_id}"),
    ("patch", "/jobs/{job_id}/review"),
    ("post", "/jobs/{job_id}/regenerate-proxy"),
    ("get", "/teams/{car_number}"),
    # Delivery
    ("get", "/manifest"),
    ("post", "/outbox/{job_id}/ack"),
    # Fleet
    ("get", "/fleet"),
    # Event log (design §17)
    ("get", "/jobs/{job_id}/events"),
    ("get", "/events"),
    # Operations (wave 3): Prometheus scrape target, admin only
    ("get", "/metrics"),
}

# Routes that must work without an auth header: the health check, and
# /enroll, where the one-time token in the body is the credential.
OPEN_ENDPOINTS = {("get", "/health"), ("post", "/enroll")}


def load_committed_contract() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def endpoints_in(document: dict) -> set[tuple[str, str]]:
    """All (method, path) pairs in an OpenAPI document."""
    return {
        (method, path) for path, operations in document["paths"].items() for method in operations
    }


def test_committed_yaml_is_up_to_date_with_the_contract_code():
    # Regenerate the YAML in memory and compare it, character by character,
    # with the committed file. If this fails, run:
    #     uv run python tools/generate_openapi.py
    # and commit the result.
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi_yaml()


def test_every_required_endpoint_is_in_the_contract():
    # Set difference: required endpoints that are NOT in the document.
    # An empty set means nothing is missing.
    missing = REQUIRED_ENDPOINTS - endpoints_in(load_committed_contract())
    assert missing == set()


def test_contract_has_no_endpoints_nobody_asked_for():
    # The reverse check: anything extra must be added to REQUIRED_ENDPOINTS
    # on purpose (and documented in docs/api.md).
    extra = endpoints_in(load_committed_contract()) - REQUIRED_ENDPOINTS
    assert extra == set()


def test_every_endpoint_except_the_open_ones_requires_auth():
    # In OpenAPI, an operation lists its accepted credentials under
    # "security". No "security" key means anyone may call it.
    document = load_committed_contract()
    unprotected = {
        (method, path)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if not operation.get("security")
    }
    assert unprotected == OPEN_ENDPOINTS


def test_all_four_auth_schemes_are_declared():
    schemes = load_committed_contract()["components"]["securitySchemes"]
    assert set(schemes) == {"workerBearer", "adminBearer", "reviewerBasic", "webhookSecret"}


def test_error_responses_use_the_standard_error_shape():
    # Spot check: a 409 from claim must point at the ErrorResponse schema,
    # so clients can rely on {"error": {"code", "message", "details"}}.
    claim = load_committed_contract()["paths"]["/jobs/{job_id}/claim"]["post"]
    schema_ref = claim["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/ErrorResponse")


def test_orchestrator_serves_no_raw_or_outbox_bytes():
    # Design §15: Caddy serves /inbox/* and /outbox/* itself; the
    # orchestrator only answers Caddy's "may this worker have it?" question.
    paths = load_committed_contract()["paths"]
    assert "/jobs/{job_id}/raw" not in paths
    assert "get" not in paths.get("/outbox/{job_id}", {})
    forward_auth = paths["/auth/file"]["get"]
    header_names = {p["name"] for p in forward_auth["parameters"] if p["in"] == "header"}
    assert "X-Forwarded-Uri" in header_names
    # 2xx allows, 401/403 deny: Caddy relies on exactly these answers.
    assert {"204", "401", "403"} <= set(forward_auth["responses"])
    assert forward_auth["security"] == [{"workerBearer": []}]


def test_enroll_documents_its_rate_limit():
    # /enroll is public, so the contract must say it can answer 429 with
    # a Retry-After header telling the worker how long to wait.
    enroll = load_committed_contract()["paths"]["/enroll"]["post"]
    too_many = enroll["responses"]["429"]
    assert "Retry-After" in too_many["headers"]
    assert "10 requests per source IP per 600 s" in enroll["description"]


def test_webhook_route_and_secret_header_are_exactly_what_evolution_is_told():
    # infra/vps/up.sh points Evolution at exactly this path with this header
    # (tests/infra/test_up_down.py checks its side); it must not drift.
    webhook = load_committed_contract()["paths"]["/intake/evolution-webhook"]["post"]
    assert webhook["security"] == [{"webhookSecret": []}]
    scheme = load_committed_contract()["components"]["securitySchemes"]["webhookSecret"]
    assert (scheme["in"], scheme["name"]) == ("header", "X-Webhook-Secret")
    # 202 = accepted for background download; 200 = ignored or duplicate.
    assert {"200", "202"} <= set(webhook["responses"])


def test_failed_endpoint_takes_a_message_and_needs_a_worker():
    failed = load_committed_contract()["paths"]["/jobs/{job_id}/failed"]["post"]
    assert failed["security"] == [{"workerBearer": []}]
    body_ref = failed["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert body_ref.endswith("/FailedRequest")


def test_failed_request_has_a_scope_that_defaults_to_worker():
    # Design §17: "worker" means "this worker could not do it", "job" means
    # "the input is bad". Leaving the field out must mean "worker", so an
    # older worker that never sends it cannot fail a job by itself.
    schemas = load_committed_contract()["components"]["schemas"]
    scope = schemas["FailedRequest"]["properties"]["scope"]
    assert scope["default"] == "worker"
    assert set(schemas["FailureScope"]["enum"]) == {"job", "worker"}


def test_event_log_routes_accept_a_reviewer_or_the_admin_only():
    # The event log is for humans (review UI, fleet page) and João's
    # scripts; workers have no business reading it.
    paths = load_committed_contract()["paths"]
    for path in ("/jobs/{job_id}/events", "/events"):
        security = paths[path]["get"]["security"]
        assert security == [{"reviewerBasic": []}, {"adminBearer": []}]
    query_names = {p["name"] for p in paths["/events"]["get"]["parameters"]}
    assert query_names == {"worker_id", "since", "limit"}


def test_job_has_a_frame_rate_and_the_proxy_upload_can_send_it():
    # The first holder reports the proxy's frame rate with the proxy upload
    # (header Frame-Rate); the job stores it so the review UI can step frames.
    document = load_committed_contract()
    assert "frame_rate" in document["components"]["schemas"]["Job"]["properties"]
    proxy_upload = document["paths"]["/jobs/{job_id}/proxy"]["post"]
    header_names = {p["name"] for p in proxy_upload["parameters"] if p["in"] == "header"}
    assert "Frame-Rate" in header_names


def test_process_task_carries_the_delivery_decision():
    # The orchestrator decides direct push vs outbox; the worker just obeys.
    schemas = load_committed_contract()["components"]["schemas"]
    assert "delivery" in schemas["ProcessTask"]["required"]
    assert set(schemas["DeliveryPlan"]["properties"]) == {"mode", "studio_url"}


def test_enroll_answer_names_the_tailnet_base_url():
    # The second origin (besides orchestrator_url) a worker may send its token to.
    schemas = load_committed_contract()["components"]["schemas"]
    assert "tailnet_base_url" in schemas["EnrollResponse"]["required"]


def test_metrics_is_prometheus_text_for_the_admin_only():
    # /metrics is scraped by an external Prometheus with the admin token
    # (infra/vps/MONITORING.md). Reviewers and workers may not read it.
    metrics = load_committed_contract()["paths"]["/metrics"]["get"]
    assert metrics["security"] == [{"adminBearer": []}]
    # The success body is Prometheus' text format, not JSON...
    assert list(metrics["responses"]["200"]["content"]) == ["text/plain; version=0.0.4"]
    # ...but a refusal is still the standard JSON error shape.
    assert "application/json" in metrics["responses"]["401"]["content"]
