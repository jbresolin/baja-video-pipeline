"""Master only: fetch raws we missed while offline from peers (design §4).

While the master is offline, two other workers hold each new raw. Jobs that
are still moving get a download_raw offer for the master when it returns
(the orchestrator always wants the master as a holder). But jobs that
finished meanwhile may never be offered again, so the master also looks
for gaps itself and copies them from a peer, because it is the single
backup source after the event (design §11).

The contract has no "which held jobs am I missing" query, and the review
queue is reviewer-only, so the gap is derived here:
    GET /manifest            every delivered clip → its job id
    minus held_job_ids       what is already on our disk
    GET /jobs/{id}           the raw's SHA-256, size, confirmed holders
    GET /workers             where those holders' file servers are
then GET http://<holder>:8710/raw/<job_id>, verified like any download.
There is no lease for this, so no holder-confirm; the next heartbeat's
held_job_ids tells the orchestrator we hold it now.
"""

import logging
import threading
import time

import httpx

from shared.http_transfer import DownloadFailedError, HashMismatchError, download_file
from shared.models import HolderStatus, MeasurementKind
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.leases import CancelToken, TaskCancelledError
from worker.logging_setup import job_context
from worker.storage import Storage
from worker.tasks.download_raw import raw_extension_from
from worker.tasks.environment import MeasurementLog

logger = logging.getLogger(__name__)

BACKFILL_FIRST_RUN_DELAY_SECONDS = 60.0
BACKFILL_INTERVAL_SECONDS = 600.0


class MasterBackfill:
    def __init__(
        self,
        *,
        api: OrchestratorClient,
        storage: Storage,
        measurements: MeasurementLog,
        transfer_client: httpx.Client,
        own_worker_id: str,
    ) -> None:
        self._api = api
        self._storage = storage
        self._measurements = measurements
        self._client = transfer_client
        self._own_worker_id = own_worker_id

    def missing_job_ids(self) -> list[str]:
        held = set(self._storage.held_job_ids())
        seen: set[str] = set()
        missing = []
        for clip in self._api.manifest().clips:
            if clip.job_id not in held and clip.job_id not in seen:
                seen.add(clip.job_id)
                missing.append(clip.job_id)
        return missing

    def run_once(self, cancel: CancelToken) -> int:
        """Copy every missing raw we can; returns how many were fetched."""
        try:
            missing = self.missing_job_ids()
            if not missing:
                return 0
            peer_urls = self._peer_file_server_urls()
        except (OrchestratorUnreachableError, ApiError) as error:
            logger.warning("backfill skipped: %s", error)
            return 0
        logger.info("backfill: %d delivered job(s) not held here", len(missing))
        fetched = 0
        for job_id in missing:
            cancel.raise_if_cancelled()
            with job_context(job_id), self._storage.pin(job_id):
                if self._fetch_one(job_id, peer_urls, cancel):
                    fetched += 1
        return fetched

    def _peer_file_server_urls(self) -> dict[str, str]:
        urls = {}
        for peer in self._api.peers().workers:
            if peer.worker_id == self._own_worker_id or not peer.online:
                continue
            if peer.tailnet_address and peer.file_server_port:
                urls[peer.worker_id] = f"http://{peer.tailnet_address}:{peer.file_server_port}"
        return urls

    def _fetch_one(self, job_id: str, peer_urls: dict[str, str], cancel: CancelToken) -> bool:
        try:
            job = self._api.get_job(job_id)
        except (OrchestratorUnreachableError, ApiError) as error:
            logger.warning("backfill: cannot look up the job: %s", error)
            return False
        if not self._storage.can_hold_new_raw(job.size_bytes):
            logger.warning("backfill: no disk space for %d bytes", job.size_bytes)
            return False
        sources = [
            f"{peer_urls[holder.worker_id]}/raw/{job_id}"
            for holder in job.holders
            if holder.status == HolderStatus.CONFIRMED and holder.worker_id in peer_urls
        ]
        if not sources:
            logger.info("backfill: no holder of this raw is online")
            return False
        destination = self._storage.raw_path(
            job_id, raw_extension_from(job.source.original_filename)
        )
        for url in sources:
            started_at = time.monotonic()
            try:
                result = download_file(
                    self._client, url, destination, job.sha256, max_attempts=3, sleep=cancel.sleep
                )
            except (DownloadFailedError, HashMismatchError, httpx.HTTPError) as error:
                logger.warning("backfill from %s failed: %s", url, error)
                continue
            self._measurements.record(
                MeasurementKind.DOWNLOAD, result.size_bytes - result.resumed_from_byte, started_at
            )
            logger.info("backfilled raw from %s (%d bytes)", url, result.size_bytes)
            return True
        return False

    def run_forever(self, shutdown: threading.Event) -> None:
        """Thread body: first pass a minute after start, then every 10 minutes."""
        cancel = CancelToken(None, shutdown)
        delay = BACKFILL_FIRST_RUN_DELAY_SECONDS
        while not shutdown.wait(delay):
            try:
                self.run_once(cancel)
            except TaskCancelledError:
                return
            except Exception:
                logger.exception("unexpected error during backfill")
            delay = BACKFILL_INTERVAL_SECONDS
