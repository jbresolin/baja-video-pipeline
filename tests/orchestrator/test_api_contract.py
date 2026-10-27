"""The service against the contract: same routes, same auth rules, same error shape."""

from orchestrator.app import create_app
from shared.models import WorkerRole
from tools.openapi_contract.app import build_contract_app


def routes_of(app) -> set[tuple[str, str]]:
    """Every (method, path) pair in an app's OpenAPI document."""
    document = app.openapi()
    return {
        (method, path) for path, operations in document["paths"].items() for method in operations
    }


def test_the_service_offers_exactly_the_contract_routes(harness):
    # orchestrator/openapi.yaml is generated from the contract stubs; the
    # real app must have the same routes, no more and no fewer.
    service = create_app(harness.context, run_background_tasks=False)
    assert routes_of(service) == routes_of(build_contract_app())


def test_health_is_open(harness):
    assert harness.client.get("/health").json() == {"status": "ok"}


def test_a_worker_cannot_use_another_workers_id(harness):
    first = harness.enroll(WorkerRole.WORKER, "first")
    second = harness.enroll(WorkerRole.WORKER, "second")
    response = harness.client.post(
        f"/workers/{second.worker_id}/heartbeat",
        json={"free_bytes": 0, "storage_cap_bytes": 0},
        headers=first.headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_wrong_reviewer_password_is_401(harness):
    response = harness.client.get("/review/queue", auth=("ana", "wrong"))
    assert response.status_code == 401


def test_get_job_accepts_a_reviewer_or_a_worker_but_not_the_admin(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    missing = "0" * 32
    # 404 (not 401) means the credential was accepted.
    assert harness.client.get(f"/jobs/{missing}", headers=worker.headers).status_code == 404
    assert harness.client.get(f"/jobs/{missing}", auth=harness.reviewer_auth).status_code == 404
    assert harness.client.get(f"/jobs/{missing}", headers=harness.admin_headers).status_code == 401


def test_fleet_accepts_a_reviewer_or_the_admin_and_reports_disk_and_counts(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    by_admin = harness.client.get("/fleet", headers=harness.admin_headers)
    by_reviewer = harness.client.get("/fleet", auth=harness.reviewer_auth)
    assert by_admin.status_code == by_reviewer.status_code == 200
    fleet = by_admin.json()
    assert fleet["workers"][0]["online"] is True
    assert fleet["server_disk"]["inbox_cap_bytes"] == 25_000_000_000
    assert fleet["jobs_by_state"] == {}


def test_invalid_bodies_get_the_standard_400_shape(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    response = harness.client.post(
        f"/workers/{worker.worker_id}/next", json={"accept_kinds": []}, headers=worker.headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_routes_get_the_standard_404_shape(harness):
    response = harness.client.get("/no/such/route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
