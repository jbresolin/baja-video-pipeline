"""Ready-made API objects (jobs, fleet, manifest, teams) for the review UI tests.

They are built with the real pydantic models from shared.models, so if a
test's sample data does not match the API contract, building it fails.
The event log for these jobs and workers is in sample_events.py.
"""

import base64
from datetime import UTC, datetime, timedelta

from shared.models import (
    ClipInfo,
    DeliveryRoute,
    FleetResponse,
    FleetWorker,
    Holder,
    HolderStatus,
    IntakeSource,
    Job,
    JobFailure,
    JobState,
    Lease,
    ManifestEntry,
    ManifestResponse,
    ReceivedAs,
    ServerDisk,
    TaskKind,
    Team,
    Worker,
    WorkerCapabilities,
    WorkerRole,
    WorkerStats,
)

# A fixed moment, so tests never depend on the real clock.
T0 = datetime(2026, 10, 25, 14, 0, 0, tzinfo=UTC)

# The reviewer the fake orchestrator accepts: user "ana", password "secret".
REVIEWER_USERNAME = "ana"
REVIEWER_AUTHORIZATION = "Basic " + base64.b64encode(b"ana:secret").decode()

MASTER_ID = "w-master-0001"
VOLUNTEER_ID = "w-volunteer-0002"
STUDIO_ID = "w-studio-0003"

GB = 1_000_000_000


def make_job(job_id: str = "job-awaiting-1", state: JobState = JobState.AWAITING_REVIEW, **changes):
    """A plausible job in `state`. Keyword arguments override any field."""
    fields = {
        "id": job_id,
        "state": state,
        "version": 3,
        "created_at": T0,
        "updated_at": T0 + timedelta(minutes=5),
        "source": IntakeSource(
            message_id=f"msg-{job_id}",
            sender_name="Bruno",
            received_as=ReceivedAs.DOCUMENT,
            original_filename="VID_0042.mp4",
            mimetype="video/mp4",
        ),
        "sha256": "a" * 64,
        "size_bytes": 512 * 1_000_000,
        "duration_s": 95.5,
        "holders": [
            Holder(
                worker_id=MASTER_ID,
                status=HolderStatus.CONFIRMED,
                assigned_at=T0 + timedelta(seconds=10),
                confirmed_at=T0 + timedelta(minutes=2),
            )
        ],
        "proxy_available": True,
        "proxy_url": f"/proxies/{job_id}.mp4",
        "thumbnail_url": f"/thumbs/{job_id}.jpg",
    }
    fields.update(changes)
    return Job(**fields)


def failed_job(job_id: str = "job-failed-1") -> Job:
    """A job a worker could not render: reviewers choose retry or discard."""
    return make_job(
        job_id,
        JobState.FAILED,
        created_at=T0 - timedelta(hours=1),
        prova="Enduro",
        car_number=42,
        passagem=2,
        cut_in_s=3.0,
        cut_out_s=40.0,
        failure=JobFailure(
            worker_id=VOLUNTEER_ID,
            task_kind=TaskKind.PROCESS,
            message="ffmpeg exited: moov atom not found",
            exit_code=1,
            failed_at=T0 + timedelta(minutes=30),
        ),
    )


def delivered_job(job_id: str = "job-done-1") -> Job:
    return make_job(
        job_id,
        JobState.DONE,
        prova="Suspensão",
        car_number=7,
        team_name="Equipe Ipê",
        passagem=1,
        cut_in_s=1.0,
        cut_out_s=20.0,
        reviewed_by="ana",
        reviewed_at=T0 + timedelta(minutes=10),
        # The last change is the cleanup right after delivery.
        updated_at=T0 + timedelta(minutes=21),
        clip=ClipInfo(
            sha256="b" * 64,
            size_bytes=80_000_000,
            relative_path="suspensao/7/1.mp4",
            route=DeliveryRoute.DIRECT,
            rendered_by_worker_id=MASTER_ID,
            delivered_at=T0 + timedelta(minutes=20),
        ),
    )


def make_teams() -> dict[int, Team]:
    return {
        42: Team(car_number=42, team_name="Equipe Cerrado", school="UnB"),
        7: Team(car_number=7, team_name="Equipe Ipê", school=None),
    }


def make_manifest() -> ManifestResponse:
    """Two delivered clips, newest first (as the orchestrator sends them)."""
    newer = ManifestEntry(
        job_id="job-done-2",
        prova="Enduro",
        car_number=42,
        team_name="Equipe Cerrado",
        passagem=3,
        relative_path="enduro/42/3.mp4",
        sha256="c" * 64,
        size_bytes=90_000_000,
        delivered_at=T0 + timedelta(minutes=40),
    )
    older = ManifestEntry(
        job_id="job-done-1",
        prova="Suspensão",
        car_number=7,
        team_name="Equipe Ipê",
        passagem=1,
        relative_path="suspensao/7/1.mp4",
        sha256="b" * 64,
        size_bytes=80_000_000,
        delivered_at=T0 + timedelta(minutes=20),
    )
    return ManifestResponse(clips=[newer, older], generated_at=T0 + timedelta(hours=1))


def _worker(worker_id, name, role, *, encoder="libx264", heartbeat=T0, stats=None, **changes):
    return Worker(
        id=worker_id,
        name=name,
        role=role,
        capabilities=WorkerCapabilities(has_nvenc=encoder == "h264_nvenc", video_encoder=encoder),
        free_bytes=changes.get("free_bytes", 300 * GB),
        storage_cap_bytes=changes.get("storage_cap_bytes", 500 * GB),
        stats=stats or WorkerStats(),
        enrolled_at=T0 - timedelta(days=2),
        last_heartbeat=heartbeat,
    )


def make_fleet() -> FleetResponse:
    master = FleetWorker(
        worker=_worker(
            MASTER_ID,
            "joao-desktop",
            WorkerRole.MASTER,
            encoder="h264_nvenc",
            stats=WorkerStats(download_mbps=92.5, process_s_per_gb=41.0, upload_mbps=38.2),
        ),
        online=True,
        leases=[
            Lease(
                lease_id="lease-1",
                job_id="job-claimed-1",
                worker_id=MASTER_ID,
                task_kind=TaskKind.PROCESS,
                acquired_at=T0,
                expires_at=T0 + timedelta(seconds=45),
            )
        ],
        held_job_count=12,
        held_bytes=6 * GB,
        accepting_new_raws=True,
    )
    volunteer = FleetWorker(
        worker=_worker(
            VOLUNTEER_ID,
            "maria-notebook",
            WorkerRole.WORKER,
            heartbeat=T0 - timedelta(minutes=7),
            free_bytes=5 * GB,
            storage_cap_bytes=100 * GB,
        ),
        online=False,
        leases=[],
        held_job_count=3,
        held_bytes=2 * GB,
        accepting_new_raws=False,
    )
    return FleetResponse(
        workers=[volunteer, master],
        server_disk=ServerDisk(
            inbox_bytes=3 * GB,
            inbox_hold_bytes=22 * GB,
            inbox_cap_bytes=25 * GB,
            proxy_bytes=4 * GB,
            outbox_bytes=0,
            free_bytes=40 * GB,
        ),
        jobs_by_state={"awaiting_review": 4, "ready": 2, "done": 30, "failed": 1},
    )
