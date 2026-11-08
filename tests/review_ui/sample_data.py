"""Ready-made API objects (jobs, fleet, manifest, teams) for the review UI tests.

They are built with the real pydantic models from shared.models, so if a
test's sample data does not match the API contract, building it fails.
The event log for these jobs and workers is in sample_events.py.
"""

from datetime import UTC, datetime, timedelta

from shared.models import (
    ClipInfo,
    ClipInputsComparison,
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
    NameStyle,
    PassClock,
    PassResult,
    PassResultFetch,
    Prova,
    ReceivedAs,
    RecordedAtSource,
    RenderClock,
    RenderInputField,
    RenderInputs,
    SenderMessage,
    SenderMessageKind,
    SenderMessageRole,
    ServerDisk,
    TaskKind,
    Team,
    Worker,
    WorkerCapabilities,
    WorkerRole,
    WorkerStats,
)
from shared.testing import credentials

# A fixed moment, so tests never depend on the real clock.
T0 = datetime(2026, 10, 25, 14, 0, 0, tzinfo=UTC)

# The reviewer the fake orchestrator accepts (placeholders, design §14).
# Signing in gives REVIEWER_SESSION_TOKEN; the browser keeps it in the
# bvp_session cookie and the UI sends it on as REVIEWER_AUTHORIZATION.
REVIEWER_USERNAME = credentials.REVIEWER_USERNAME
REVIEWER_PASSWORD = credentials.PLACEHOLDER_PASSWORD
REVIEWER_SESSION_TOKEN = credentials.PLACEHOLDER_SESSION_TOKEN
REVIEWER_AUTHORIZATION = f"Bearer {REVIEWER_SESSION_TOKEN}"
SIGNED_IN_COOKIES = {"bvp_session": REVIEWER_SESSION_TOKEN}

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


def on_event_day(hour: int, minute: int) -> datetime:
    """14:02 on the track on 2026-03-26 (São Paulo, UTC-3) as a UTC datetime."""
    return datetime(2026, 3, 26, hour + 3, minute, tzinfo=UTC)


def car_7_video(
    job_id: str,
    filmed_at: datetime,
    *,
    passagem: int | None = None,
    state: JobState = JobState.AWAITING_REVIEW,
    source: RecordedAtSource = RecordedAtSource.METADATA,
    prova: str = "Suspensão",
) -> Job:
    """A video of Baja UFPR's car 7 in Suspensão, filmed at `filmed_at`
    (LIVESTACK-45). It arrived 40 minutes after it was filmed."""
    return make_job(
        job_id,
        state,
        created_at=filmed_at + timedelta(minutes=40),
        updated_at=filmed_at + timedelta(minutes=41),
        recorded_at=filmed_at,
        recorded_at_source=source,
        prova=prova,
        car_number=7,
        team_name="Baja UFPR",
        passagem=passagem,
    )


def rendered_inputs(**changes) -> RenderInputs:
    """What delivered_job()'s clip was rendered from (the worker's report)."""
    fields = {
        "raw_sha256": "a" * 64,
        "cut_in_s": 1.0,
        "cut_out_s": 20.0,
        "prova": "Suspensão",
        "car_number": 7,
        "passagem": 1,
        "show_pass": True,
        "team_name_style": NameStyle.SHORT,
        "school_name_style": NameStyle.SHORT,
        "overlay_team_name": "Equipe Ipê",
        "overlay_school_name": None,
        "overlay_theme_version": "7" * 64,
        "reported_by_worker": True,
    }
    fields.update(changes)
    return RenderInputs(**fields)


def comparison(clip: RenderInputs, now: RenderInputs, *changed: RenderInputField, version=1):
    """Job.clip_inputs as the orchestrator computes it."""
    return ClipInputsComparison(version=version, clip=clip, now=now, changed=list(changed))


def edited_after_delivery_job(job_id: str = "job-edited-1") -> Job:
    """delivered_job() whose cut-in a reviewer moved from 1 s to 3 s after delivery
    (LIVESTACK-43): the clip on air no longer matches the review."""
    clip = rendered_inputs()
    now = rendered_inputs(cut_in_s=3.0, overlay_theme_version=None, reported_by_worker=False)
    return delivered_job(job_id).model_copy(
        update={
            "cut_in_s": 3.0,
            "clip_inputs": comparison(clip, now, RenderInputField.CUT),
        }
    )


def make_teams() -> dict[int, Team]:
    return {
        # clip_*: what the real orchestrator adds under the default name
        # choices (short, falling back to the full name when there is none).
        42: Team(
            car_number=42,
            team_name="Equipe Cerrado",
            school="UnB",
            clip_team_name="Equipe Cerrado",
            clip_school_name="UnB",
        ),
        7: Team(car_number=7, team_name="Equipe Ipê", school=None, clip_team_name="Equipe Ipê"),
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
        # Its passagem was changed to 2 after delivery (LIVESTACK-43).
        clip_inputs=comparison(
            rendered_inputs(),
            rendered_inputs(passagem=2, overlay_theme_version=None),
            RenderInputField.PASSAGEM,
        ),
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


def make_provas() -> list[Prova]:
    """The provas list: two active, one deactivated."""
    return [
        Prova(id=1, name="Enduro", slug="enduro", display_order=1, active=True),
        Prova(id=2, name="Suspensão", slug="suspensao", display_order=2, active=True),
        Prova(id=3, name="Lama", slug="lama", display_order=3, active=False),
    ]


def sender_message(
    role: SenderMessageRole,
    text: str | None,
    minutes_after_t0: int,
    *,
    kind: SenderMessageKind = SenderMessageKind.TEXT,
    message_id: str | None = None,
    **changes,
) -> SenderMessage:
    """One of the sender's messages as GET /jobs/{id}/sender-messages lists it (LIVESTACK-49)."""
    return SenderMessage(
        message_id=message_id or f"3EB0{role.upper()}{minutes_after_t0:04d}",
        role=role,
        kind=kind,
        text=text,
        received_at=T0 + timedelta(minutes=minutes_after_t0),
        **changes,
    )


def bruno_messages() -> list[SenderMessage]:
    """What Bruno wrote around job-awaiting-1 (received at T0), in display order."""
    return [
        sender_message(SenderMessageRole.CAPTION, "carro 42", 0, kind=SenderMessageKind.VIDEO),
        sender_message(SenderMessageRole.AFTER, "segunda passagem", 1),
        sender_message(SenderMessageRole.AFTER, None, 2, kind=SenderMessageKind.AUDIO),
        sender_message(SenderMessageRole.AFTER, "o 42 de novo", 3, edited=True),
        sender_message(SenderMessageRole.AFTER, None, 4, deleted=True),
        sender_message(SenderMessageRole.BEFORE, "vou mandar o 42 agora", -5),
    ]


# --- Official pass results (LIVESTACK-47) ---


def pass_result(**changes) -> PassResult:
    """Job.pass_result for a Manobrabilidade job whose result is published.

    Car 26's second run: "66 s · 4 cones → 86 s", shown, fetched at T0 and
    fetching fine. Keyword arguments override any field.
    """
    fields = {
        "configured": True,
        "show": True,
        "text": "66 s · 4 cones → 86 s",
        "fetch": PassResultFetch(
            resultado_id="26BR_MAN",
            last_attempt_at=T0,
            last_attempt_ok=True,
            last_success_at=T0,
        ),
    }
    fields.update(changes)
    return PassResult(**fields)


def failing_fetch(**changes) -> PassResultFetch:
    """The results API down since 14:02 (São Paulo), last good answer at 13:58."""
    good = datetime(2026, 10, 25, 16, 58, tzinfo=UTC)
    since = datetime(2026, 10, 25, 17, 2, tzinfo=UTC)
    fields = {
        "resultado_id": "26BR_MAN",
        "last_attempt_at": since + timedelta(seconds=30),
        "last_attempt_ok": False,
        "last_error": "results API unreachable: connection refused",
        "last_success_at": good,
        "failing_since": since,
        "stale": True,
    }
    fields.update(changes)
    return PassResultFetch(**fields)


def pass_result_job(job_id: str = "job-result-1", **changes) -> Job:
    """A Manobrabilidade job (car 26, passagem 2) with its official result."""
    fields = {
        "prova": "Manobrabilidade",
        "car_number": 26,
        "passagem": 2,
        "pass_result": pass_result(),
    }
    fields.update(changes)
    return make_job(job_id, **fields)


def pass_clock(**changes) -> PassClock:
    """Job.pass_clock for car 26's second Manobrabilidade run (LIVESTACK-48).

    The official time is 66 s, shown with one decimal ("66,0", 660 units);
    the largada is marked at 12.345 s and the toggle is on, so a render
    would draw the clock. Keyword arguments override any field.
    """
    fields = {
        "configured": True,
        "show": True,
        "largada_s": 12.345,
        "decimals": 1,
        "unit": "s",
        "official_units": 660,
        "official_text": "66,0",
        "fetch": PassResultFetch(
            resultado_id="26BR_MAN",
            last_attempt_at=T0,
            last_attempt_ok=True,
            last_success_at=T0,
        ),
        "render": RenderClock(largada_s=12.345, official_units=660, decimals=1, unit="s"),
    }
    fields.update(changes)
    return PassClock(**fields)


def pass_clock_job(job_id: str = "job-clock-1", **changes) -> Job:
    """pass_result_job with the running clock too (largada marked, toggle on)."""
    fields = {
        "largada_s": 12.345,
        "pass_clock": pass_clock(),
        "clip_team_name": "Saci Baja",
        "clip_school_name": "UNIFEI",
    }
    fields.update(changes)
    return pass_result_job(job_id, **fields)
