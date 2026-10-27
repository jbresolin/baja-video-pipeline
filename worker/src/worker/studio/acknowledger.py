"""After a direct push: update latest.m3u, then ACK to the orchestrator.

docs/api.md §7.2: once the receiver stored a pushed clip it answers the
worker `complete: true` straight away, and then calls POST
/jobs/{id}/delivered, "retrying in the background until it succeeds;
repeating it is harmless". This module is that background part. It also
looks the job up (GET /jobs/{id}) for the playlist titles first.

Pending ACKs live in memory only. If the studio restarts before one is
sent, the pushing worker sees its lease still renewed after 60 s and
uploads to the outbox, so the clip still arrives (via the deliver task).
"""

import logging
import queue
import threading
from dataclasses import dataclass

from shared.models import DeliveredRequest
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.logging_setup import job_context
from worker.studio.library import StudioLibrary

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 10.0


@dataclass
class PendingDelivery:
    job_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    # Set once the clip is in latest.m3u, so an ACK retry does not move it
    # back above clips that arrived in the meantime.
    in_playlist: bool = False


class DeliveryAcknowledger:
    def __init__(
        self,
        api: OrchestratorClient,
        library: StudioLibrary,
        shutdown: threading.Event,
        retry_delay_seconds: float = RETRY_DELAY_SECONDS,
    ) -> None:
        self._api = api
        self._library = library
        self._shutdown = shutdown
        self._retry_delay_seconds = retry_delay_seconds
        self._pending: queue.Queue[PendingDelivery] = queue.Queue()

    def submit(self, delivery: PendingDelivery) -> None:
        self._pending.put(delivery)

    def pending_count(self) -> int:
        return self._pending.qsize()

    def run_forever(self) -> None:
        """Thread body: handle deliveries until shutdown."""
        while not self._shutdown.is_set():
            try:
                delivery = self._pending.get(timeout=0.5)
            except queue.Empty:
                continue
            while not self._shutdown.is_set() and not self.handle(delivery):
                self._shutdown.wait(self._retry_delay_seconds)

    def handle_all_pending(self) -> None:
        """Try every queued delivery once; unsuccessful ones are queued again (tests)."""
        for _ in range(self._pending.qsize()):
            delivery = self._pending.get_nowait()
            if not self.handle(delivery):
                self._pending.put(delivery)

    def handle(self, delivery: PendingDelivery) -> bool:
        """Playlist + ACK for one clip; False means "try again later"."""
        with job_context(delivery.job_id):
            if not delivery.in_playlist:
                self._add_to_playlist(delivery)

            request = DeliveredRequest(
                sha256=delivery.sha256,
                size_bytes=delivery.size_bytes,
                relative_path=delivery.relative_path,
            )
            try:
                answer = self._api.delivered(delivery.job_id, request)
            except OrchestratorUnreachableError as error:
                logger.warning("delivered ACK not sent yet (%s); will retry", error)
                return False
            except ApiError as error:
                # E.g. the job was reprocessed meanwhile: nothing to retry.
                logger.warning("orchestrator refused the delivered ACK: %s", error)
                return True
            logger.info("delivered ACK sent; job is now %s", answer.state)
            return True

    def _add_to_playlist(self, delivery: PendingDelivery) -> None:
        job = None
        try:
            job = self._api.get_job(delivery.job_id)
        except (OrchestratorUnreachableError, ApiError) as error:
            logger.warning("cannot look up the job for the playlist title: %s", error)
        self._library.record_clip(delivery.relative_path, job)
        delivery.in_playlist = True
