"""Who may do which task on which job (design §4, §5; docs/api.md §5).

evaluate_task() is the single rulebook used both by /next (to make an
offer) and by /claim (to accept it), so the two can never disagree.
archive_clip's rules live in archive_rules.py. It
answers with the task to hand out, or with why not:

    ALREADY_CLAIMED  another worker holds the lease (or the holder slots)
    NOT_CLAIMABLE    wrong state, wrong role, not a holder, excluded, ...

Leases held by the asking worker itself never block it: claiming twice
returns the same lease.
"""

import sqlite3
from datetime import timedelta

from orchestrator.db.database import from_db_time
from orchestrator.domain.archive_rules import evaluate_archive_clip
from orchestrator.domain.clip_archive import next_clip_relative_path
from orchestrator.domain.clip_downloads import evaluate_upload_clip
from orchestrator.domain.clip_names import clip_names_for_job, name_styles
from orchestrator.domain.fleet_snapshot import (
    FleetSnapshot,
    accepts_new_raws,
    on_shared_tailnet,
    peer_raw_url,
)
from orchestrator.domain.originals import evaluate_upload_original
from orchestrator.domain.pass_clock import clock_for_render
from orchestrator.domain.pass_results import pass_text_for_render
from orchestrator.domain.render_features import can_render, required_render_features
from orchestrator.domain.task_decision import TaskDecision, refuse
from orchestrator.repositories import app_settings as settings_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout
from shared.models import (
    DeliverTask,
    DeliveryPlan,
    DeliveryRoute,
    DownloadRawTask,
    ErrorCode,
    Framing,
    HolderStatus,
    JobState,
    MakeProxyTask,
    ProcessTask,
    TaskKind,
    WorkerRole,
)
from shared.protocol import REQUIRED_HOLDER_COUNT

# For this long after intake, a non-master holder slot is reserved for the
# fastest idle downloader (rolling download Mbps). After it, any eligible
# worker may take it, so a slow fleet never leaves a raw on the VPS alone.
FASTEST_DOWNLOADER_PREFERENCE = timedelta(seconds=30)
NO_DOWNLOAD_STATES = frozenset({JobState.DISCARDED, JobState.FAILED})
# The master backfills every raw that was not discarded (design §17), so a
# failed job is still worth downloading for it; for everyone else it is not.
NO_MASTER_DOWNLOAD_STATES = frozenset({JobState.DISCARDED})


def evaluate_task(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
    kind: TaskKind,
) -> TaskDecision:
    if worker_row["paused"]:
        return refuse(ErrorCode.NOT_CLAIMABLE, "this worker is paused")
    is_studio = worker_row["role"] == WorkerRole.STUDIO
    if kind == TaskKind.DELIVER:
        if not is_studio:
            return refuse(ErrorCode.NOT_CLAIMABLE, "only the studio delivers")
        return _evaluate_deliver(connection, layout, job_row, worker_row)
    if kind == TaskKind.UPLOAD_CLIP:
        # "Baixar clipe" (LIVESTACK-52): the studio too, as a last resort.
        return evaluate_upload_clip(connection, layout, snapshot, job_row, worker_row)
    if is_studio:
        return refuse(ErrorCode.NOT_CLAIMABLE, "the studio never holds or processes raws")
    if kind == TaskKind.ARCHIVE_CLIP:
        return evaluate_archive_clip(connection, layout, snapshot, job_row, worker_row)
    if leases_repo.is_task_excluded(connection, job_row["id"], worker_row["id"], kind):
        return refuse(ErrorCode.NOT_CLAIMABLE, f"this worker already failed {kind} on this job")
    if kind == TaskKind.DOWNLOAD_RAW:
        return _evaluate_download(connection, layout, snapshot, job_row, worker_row)
    if kind == TaskKind.UPLOAD_ORIGINAL:
        return evaluate_upload_original(connection, layout, snapshot, job_row, worker_row)

    # make_proxy and process are for confirmed holders only (reprocess included).
    holders = {h.worker_id: h for h in leases_repo.list_holders(connection, job_row["id"])}
    mine = holders.get(worker_row["id"])
    if mine is None or mine.status != HolderStatus.CONFIRMED:
        return refuse(ErrorCode.NOT_CLAIMABLE, "only a confirmed holder of the raw can do this")
    blocking = _lease_held_by_someone_else(connection, job_row["id"], worker_row["id"], kind)
    if blocking:
        return refuse(ErrorCode.ALREADY_CLAIMED, f"worker {blocking} holds the {kind} lease")
    if kind == TaskKind.MAKE_PROXY:
        return _evaluate_make_proxy(layout, job_row)
    return _evaluate_process(connection, layout, snapshot, job_row, worker_row)


def _lease_held_by_someone_else(
    connection: sqlite3.Connection, job_id: str, worker_id: str, kind: TaskKind
) -> str | None:
    for lease in leases_repo.list_leases_for_job(connection, job_id, kind):
        if lease.worker_id != worker_id:
            return lease.worker_id
    return None


def _evaluate_make_proxy(layout: VpsLayout, job_row: sqlite3.Row) -> TaskDecision:
    state = JobState(job_row["state"])
    wanted = state == JobState.RECEIVED or (
        bool(job_row["proxy_requested"]) and state not in NO_DOWNLOAD_STATES
    )
    if not wanted:
        return refuse(ErrorCode.NOT_CLAIMABLE, "this job needs no proxy")
    job_id = job_row["id"]
    return TaskDecision(
        MakeProxyTask(
            job_id=job_id,
            raw_sha256=job_row["sha256"],
            proxy_upload_url=layout.api_url(f"/jobs/{job_id}/proxy"),
            thumbnail_upload_url=layout.api_url(f"/jobs/{job_id}/thumbnail"),
            media_info_upload_url=layout.api_url(f"/jobs/{job_id}/media-info"),
        )
    )


def delivery_plan(
    snapshot: FleetSnapshot, job_id: str, worker_row: sqlite3.Row | None = None
) -> DeliveryPlan:
    """Direct push when a studio is online (heartbeat within 45 s), else the outbox.

    A worker on a shared tailnet (LIVESTACK-41 item 4) is offered the push
    too: the studio may well be shared into its tailnet (João's desktop
    pushes to the studio that way), and the orchestrator cannot know. The
    worker asks the studio's /health first (3 s) and uses the outbox when
    it does not answer (item 13), so an unreachable studio costs seconds,
    not the 60 s ACK wait. `worker_row` is kept for that reason's record.
    """
    studio_url = snapshot.studio_receive_url(job_id)
    if studio_url is None:
        return DeliveryPlan(mode=DeliveryRoute.OUTBOX, studio_url=None)
    return DeliveryPlan(mode=DeliveryRoute.DIRECT, studio_url=studio_url)


def clip_previews_enabled(connection: sqlite3.Connection) -> bool:
    """Settings "Gerar prévia dos clipes" (LIVESTACK-52) as stored; on before the first save."""
    return bool(settings_repo.load_settings_values(connection).get("clip_previews_enabled", True))


def _evaluate_process(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
) -> TaskDecision:
    previews_enabled = clip_previews_enabled(connection)
    state = JobState(job_row["state"])
    if state == JobState.CLAIMED:
        # Only reachable here when the process lease is the caller's own.
        mine = leases_repo.list_leases_for_job(connection, job_row["id"], TaskKind.PROCESS)
        if not mine:
            return refuse(ErrorCode.NOT_CLAIMABLE, "job is claimed")
    elif state != JobState.READY:
        return refuse(ErrorCode.NOT_CLAIMABLE, f"job is '{state}', not ready")
    # The official result line (LIVESTACK-47), from the latest results
    # fetched; None when there is none to show (the pill is then absent).
    pass_result_text = pass_text_for_render(connection, job_row)
    # The running clock (LIVESTACK-48); None without a largada, an official
    # time, or with the toggle off.
    pass_clock = clock_for_render(connection, job_row)
    # Render features (LIVESTACK-50): a worker version that cannot render
    # the job's options (or draw the result pill, or the clock) would
    # ignore them and make the old clip.
    required = required_render_features(job_row, pass_result_text, pass_clock)
    if not can_render(worker_row, required):
        return refuse(
            ErrorCode.NOT_CLAIMABLE,
            "this worker's version cannot render this job: it needs " + ", ".join(required),
        )
    job_id = job_row["id"]
    # The overlay text, by the Settings' name choices (full or short).
    names = clip_names_for_job(name_styles(connection), job_row)
    return TaskDecision(
        ProcessTask(
            job_id=job_id,
            raw_sha256=job_row["sha256"],
            has_audio=bool(job_row["has_audio"]),
            low_res=bool(job_row["low_res"]),
            cut_in_s=job_row["cut_in_s"],
            cut_out_s=job_row["cut_out_s"],
            prova=job_row["prova"],
            car_number=job_row["car_number"],
            # The overlay's name pill and the school in its role pill. Despite
            # the field's name, school_short is the full school name when the
            # admin chose "completo" (kept so workers need no change).
            team_name=names.team,
            school_short=names.school,
            passagem=job_row["passagem"],
            show_pass=bool(job_row["show_pass"]),
            clip_relative_path=next_clip_relative_path(connection, job_row),
            outbox_upload_url=layout.api_url(f"/jobs/{job_id}/clip"),
            delivery=delivery_plan(snapshot, job_id, worker_row),
            rotation=job_row["rotation"],
            framing=Framing(job_row["framing"]),
            crop_position=job_row["crop_position"],
            no_audio=bool(job_row["no_audio"]),
            pass_result_text=pass_result_text,
            pass_clock=pass_clock,
            required_render_features=required,
            # "Ver clipe" (LIVESTACK-52): only while the Settings switch is on.
            preview_upload_url=(
                layout.api_url(f"/jobs/{job_id}/clip-preview") if previews_enabled else None
            ),
        )
    )


def _evaluate_deliver(
    connection: sqlite3.Connection, layout: VpsLayout, job_row: sqlite3.Row, worker_row: sqlite3.Row
) -> TaskDecision:
    job_id = job_row["id"]
    if job_row["state"] != JobState.UPLOADED or job_row["clip_route"] != "outbox":
        return refuse(ErrorCode.NOT_CLAIMABLE, "no outbox clip waiting for this job")
    if not layout.outbox_path(job_id).exists():
        return refuse(ErrorCode.NOT_CLAIMABLE, "the outbox file is missing")
    blocking = _lease_held_by_someone_else(connection, job_id, worker_row["id"], TaskKind.DELIVER)
    if blocking:
        return refuse(ErrorCode.ALREADY_CLAIMED, f"worker {blocking} is delivering it")
    return TaskDecision(
        DeliverTask(
            job_id=job_id,
            download_url=layout.outbox_download_url(job_id),
            sha256=job_row["clip_sha256"],
            size_bytes=job_row["clip_size_bytes"],
            clip_relative_path=job_row["clip_relative_path"],
        )
    )


def _evaluate_download(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
) -> TaskDecision:
    job_id = job_row["id"]
    worker_id = worker_row["id"]
    closed_states = (
        NO_MASTER_DOWNLOAD_STATES if snapshot.is_master(worker_id) else NO_DOWNLOAD_STATES
    )
    if JobState(job_row["state"]) in closed_states:
        return refuse(ErrorCode.NOT_CLAIMABLE, f"job is '{job_row['state']}'")
    if not accepts_new_raws(worker_row):
        return refuse(ErrorCode.NOT_CLAIMABLE, "worker is near its storage cap")

    holders = leases_repo.list_holders(connection, job_id)
    mine = next((h for h in holders if h.worker_id == worker_id), None)
    if mine is not None and mine.status == HolderStatus.CONFIRMED:
        return refuse(ErrorCode.NOT_CLAIMABLE, "worker already holds this raw")

    if mine is None and not snapshot.is_master(worker_id):
        if on_shared_tailnet(worker_row):
            # Item 4: peers could never fetch from it, so it would be a
            # holder in name only.
            return refuse(ErrorCode.NOT_CLAIMABLE, "a worker on a shared tailnet holds no raws")
        # Design §4: the master is always a holder; one more worker joins it.
        # With the master offline, two non-master workers hold the raw.
        master_counts = any(snapshot.is_master(h.worker_id) for h in holders)
        master_counts = master_counts or snapshot.any_master_online()
        others = [h for h in holders if not snapshot.is_master(h.worker_id)]
        needed = REQUIRED_HOLDER_COUNT - 1 if master_counts else REQUIRED_HOLDER_COUNT
        if len(others) >= needed:
            if any(h.status == HolderStatus.ASSIGNED for h in others):
                return refuse(ErrorCode.ALREADY_CLAIMED, "another worker is downloading it")
            return refuse(ErrorCode.NOT_CLAIMABLE, "this raw has enough holders")
        if _faster_idle_worker_exists(connection, snapshot, job_row, holders, worker_id):
            return refuse(ErrorCode.NOT_CLAIMABLE, "a faster downloader is preferred for now")

    urls = _raw_sources(layout, snapshot, job_row, holders, worker_id)
    if not urls:
        return refuse(ErrorCode.NOT_CLAIMABLE, "no copy of the raw is reachable right now")
    return TaskDecision(
        DownloadRawTask(
            job_id=job_id,
            sha256=job_row["sha256"],
            size_bytes=job_row["size_bytes"],
            download_url=urls[0],
            fallback_download_urls=urls[1:],
            raw_file_name=job_row["raw_file_name"],
        )
    )


def _faster_idle_worker_exists(
    connection: sqlite3.Connection,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    holders,
    asking_worker_id: str,
) -> bool:
    """True while the preference window lasts and an idle eligible volunteer
    has a *measured* download speed above the asking worker's.

    Unmeasured workers count as slowest; between equals nobody is held
    back, so a fleet without stats yet simply takes turns.
    """
    created_at = from_db_time(job_row["created_at"])
    if snapshot.now - created_at >= FASTEST_DOWNLOADER_PREFERENCE:
        return False

    def speed(worker_id: str) -> float:
        measured = snapshot.download_mbps_by_worker.get(worker_id)
        return -1.0 if measured is None else measured

    holder_ids = {h.worker_id for h in holders}
    for worker_id, row in snapshot.workers_by_id.items():
        idle_and_eligible = (
            worker_id != asking_worker_id
            and row["role"] == WorkerRole.WORKER
            and snapshot.is_available_for_work(worker_id)
            and accepts_new_raws(row)
            and worker_id not in holder_ids
            and not on_shared_tailnet(row)
            and worker_id not in snapshot.downloading_worker_ids
            and not leases_repo.is_task_excluded(
                connection, job_row["id"], worker_id, TaskKind.DOWNLOAD_RAW
            )
        )
        if idle_and_eligible and speed(worker_id) > speed(asking_worker_id):
            return True
    return False


def _raw_sources(
    layout: VpsLayout, snapshot: FleetSnapshot, job_row: sqlite3.Row, holders, worker_id: str
) -> list[str]:
    """The VPS inbox first (while it has the raw), then online confirmed peers.

    A worker on a shared tailnet (item 4) reaches only the VPS, so it gets no peer URLs.
    """
    urls = []
    if job_row["raw_on_vps"]:
        urls.append(layout.inbox_download_url(job_row["id"], job_row["raw_extension"]))
    asker = snapshot.workers_by_id.get(worker_id)
    if asker is not None and on_shared_tailnet(asker):
        return urls
    for holder in holders:
        if holder.status != HolderStatus.CONFIRMED or holder.worker_id == worker_id:
            continue
        if not snapshot.is_online(holder.worker_id):
            continue
        url = peer_raw_url(snapshot.workers_by_id[holder.worker_id], job_row["id"])
        if url:
            urls.append(url)
    return urls
