"""Contract: GET /metrics, operational numbers in Prometheus text format.

Added in wave 3 (deployment hardening) so an external Prometheus/Grafana
can watch the event without new services on the VPS
(infra/vps/MONITORING.md). Admin token only: it reveals fleet size and
disk use, which reviewers and workers do not need.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from tools.openapi_contract.auth import admin_bearer, error_responses, not_implemented

router = APIRouter()


@router.get(
    "/metrics",
    tags=["operations"],
    summary="Jobs per state, VPS disk, workers online, task counters (Prometheus text)",
    # Plain Response: the 200 body is text (declared below), errors stay JSON.
    response_class=Response,
    dependencies=[Depends(admin_bearer)],
    responses={
        200: {
            "description": "Prometheus text exposition format 0.0.4",
            "content": {"text/plain; version=0.0.4": {"schema": {"type": "string"}}},
        },
        **error_responses(401),
    },
)
def metrics() -> str:
    """Gauges (read on every scrape):

    * `bvp_jobs{state}`: jobs per state, every state listed (0 included).
    * `bvp_workers_enrolled{role}`, `bvp_workers_online{role}`: online =
      heartbeat within the 45 s lease TTL.
    * `bvp_storage_bytes{area="inbox"|"proxies"|"outbox"}` (proxies includes
      thumbnails), `bvp_disk_free_bytes`, `bvp_inbox_hold_bytes`,
      `bvp_inbox_cap_bytes`.

    Counters (since the orchestrator started; reset on restart):

    * `bvp_task_offers_total{kind}`: tasks offered by `/workers/{id}/next`.
    * `bvp_task_claims_total{kind}`: new leases created by `/claim`.
    * `bvp_task_failures_total{kind,scope}`: reports accepted by `/failed`.
    """
    not_implemented()
