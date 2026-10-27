"""After a direct push: update latest.m3u, then ACK to the orchestrator.

docs/api.md §7.2: once the receiver stored a pushed clip it answers the
worker `complete: true` straight away, and then calls POST
/jobs/{id}/delivered, "retrying in the background until it succeeds;
repeating it is harmless". This module is that background part. It also
looks the job up (GET /jobs/{id}) for the playlist titles first.

Pending ACKs survive a restart (design §17): every change to the list is
written to `pending_acks.json` in the studio's working folder
(worker.studio.pending_acks), and the list is read back at start, so the
first round after a restart sends whatever was still owed.

When an ACK leaves the list:
  * 2xx: accepted.
  * 4xx other than the ones below: the orchestrator says the ACK no longer
    applies (404 the job is gone, 409 it was reprocessed or discarded
    meanwhile, 422 the hash or path does not match what it expects).
    Sending it again would get the same answer, so it is dropped with a
    warning. If the pushing worker is still waiting, it falls back to the
    outbox after 60 s, so nothing is lost.
It stays (and is retried every 10 s) on a network error or 5xx, and also
on 401/403/408/429: those say "not now" or "fix the setup", not "this ACK
is wrong", and are worth replaying once that is sorted out.
"""

import logging
import threading
from pathlib import Path

from shared.models import DeliveredRequest
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.logging_setup import job_context
from worker.studio.library import StudioLibrary
from worker.studio.pending_acks import PendingAckFile, PendingDelivery

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 10.0
# Refusals that do not mean "this ACK is wrong": keep it and try again later.
RETRYABLE_REFUSAL_STATUSES = frozenset({401, 403, 408, 429})

__all__ = ["DeliveryAcknowledger", "PendingDelivery"]


class DeliveryAcknowledger:
    def __init__(
        self,
        api: OrchestratorClient,
        library: StudioLibrary,
        shutdown: threading.Event,
        retry_delay_seconds: float = RETRY_DELAY_SECONDS,
        pending_acks_path: Path | None = None,
    ) -> None:
        """`pending_acks_path=None` keeps the list in memory only (some tests)."""
        self._api = api
        self._library = library
        self._shutdown = shutdown
        self._retry_delay_seconds = retry_delay_seconds
        self._file = PendingAckFile(pending_acks_path) if pending_acks_path else None
        # Guards _pending and the file: the receiver submits from its
        # request threads while the ACK thread works through the list.
        self._lock = threading.Lock()
        self._pending: list[PendingDelivery] = self._file.load() if self._file else []
        self._new_delivery = threading.Event()
        if self._pending:
            logger.info("%d delivered ACK(s) left from the last run; replaying", len(self._pending))

    def submit(self, delivery: PendingDelivery) -> None:
        with self._lock:
            # A newer push of the same job (reprocessed clip) replaces an
            # older ACK that was still waiting: only the latest one counts.
            self._pending = [d for d in self._pending if d.job_id != delivery.job_id]
            self._pending.append(delivery)
            self._save_locked()
        self._new_delivery.set()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def run_forever(self) -> None:
        """Thread body: handle deliveries until shutdown."""
        while not self._shutdown.is_set():
            if self.pending_count() == 0:
                self._new_delivery.wait(0.5)
                self._new_delivery.clear()
                continue
            if not self.handle_all_pending():
                self._shutdown.wait(self._retry_delay_seconds)

    def handle_all_pending(self) -> bool:
        """Try every pending delivery once, oldest first. True when none is left."""
        with self._lock:
            snapshot = list(self._pending)
        for delivery in snapshot:
            if self._shutdown.is_set():
                break
            finished = self.handle(delivery)
            with self._lock:
                if finished:
                    # By identity: a newer submit for the same job stays.
                    self._pending = [d for d in self._pending if d is not delivery]
                self._save_locked()  # also records in_playlist for a replay
        return self.pending_count() == 0

    def handle(self, delivery: PendingDelivery) -> bool:
        """Playlist + ACK for one clip; False means "keep it and try again later"."""
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
                if error.status_code in RETRYABLE_REFUSAL_STATUSES:
                    logger.warning("delivered ACK refused for now (%s); will retry", error)
                    return False
                logger.warning("delivered ACK no longer applies, dropping it: %s", error)
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

    def _save_locked(self) -> None:
        if self._file is not None:
            self._file.save(self._pending)
