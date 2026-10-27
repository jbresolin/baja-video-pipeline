"""The orchestrator's event log for the sample jobs and workers in sample_data.py.

Built with the real shared.models.Event, so an event that does not match
the API contract fails to build.
"""

from datetime import timedelta

from shared.models import Event, JobState
from tests.review_ui.sample_data import MASTER_ID, STUDIO_ID, T0, VOLUNTEER_ID


def _event(
    at, actor, kind, *, job_id=None, worker_id=None, from_state=None, to_state=None, reason=None
) -> dict:
    """One log entry without its id; make_events numbers them in order."""
    return {
        "at": at,
        "actor": actor,
        "kind": kind,
        "job_id": job_id,
        "worker_id": worker_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
    }


def make_events() -> list[Event]:
    """The orchestrator's event log for the sample jobs and workers, oldest first.

    * Both workers were enrolled by the admin two days before T0.
    * "job-done-1" went the whole way: received from WhatsApp, held and
      proxied by the master, reviewed by ana, rendered by the master,
      delivered to the studio.
    * "job-awaiting-1" was received and proxied; it waits for review.
    * "job-failed-1" failed on the volunteer (ffmpeg error), and later the
      system expired one of the volunteer's leases on another job.

    The master is mentioned in 7 events, more than the fleet page's 5, so
    tests can check that only the newest few are shown.
    """
    done, awaiting, failed = "job-done-1", "job-awaiting-1", "job-failed-1"
    master_actor, volunteer_actor = f"worker:{MASTER_ID}", f"worker:{VOLUNTEER_ID}"
    minute = timedelta(minutes=1)
    entries = [
        _event(T0 - timedelta(days=2), "admin", "worker_enrolled", worker_id=MASTER_ID),
        _event(T0 - timedelta(days=2), "admin", "worker_enrolled", worker_id=VOLUNTEER_ID),
        _event(
            T0,
            "evolution",
            "state_changed",
            job_id=done,
            to_state=JobState.RECEIVED,
            reason="WhatsApp document from Bruno",
        ),
        _event(
            T0 + timedelta(seconds=10),
            master_actor,
            "lease_acquired",
            job_id=done,
            worker_id=MASTER_ID,
            reason="download_raw",
        ),
        _event(T0 + 2 * minute, master_actor, "holder_confirmed", job_id=done, worker_id=MASTER_ID),
        _event(
            T0 + 3 * minute,
            master_actor,
            "state_changed",
            job_id=done,
            worker_id=MASTER_ID,
            from_state=JobState.RECEIVED,
            to_state=JobState.PROXY_READY,
            reason="proxy uploaded",
        ),
        _event(
            T0 + 3 * minute,
            "system",
            "state_changed",
            job_id=done,
            from_state=JobState.PROXY_READY,
            to_state=JobState.AWAITING_REVIEW,
        ),
        _event(
            T0 + 4 * minute,
            "evolution",
            "state_changed",
            job_id=awaiting,
            to_state=JobState.RECEIVED,
        ),
        _event(
            T0 + 5 * minute,
            "system",
            "state_changed",
            job_id=awaiting,
            from_state=JobState.PROXY_READY,
            to_state=JobState.AWAITING_REVIEW,
        ),
        _event(
            T0 + 10 * minute,
            "reviewer:ana",
            "state_changed",
            job_id=done,
            from_state=JobState.AWAITING_REVIEW,
            to_state=JobState.READY,
        ),
        _event(
            T0 + 11 * minute,
            master_actor,
            "lease_acquired",
            job_id=done,
            worker_id=MASTER_ID,
            reason="process",
        ),
        _event(
            T0 + 11 * minute,
            master_actor,
            "state_changed",
            job_id=done,
            worker_id=MASTER_ID,
            from_state=JobState.READY,
            to_state=JobState.CLAIMED,
        ),
        _event(
            T0 + 18 * minute,
            master_actor,
            "state_changed",
            job_id=done,
            worker_id=MASTER_ID,
            from_state=JobState.CLAIMED,
            to_state=JobState.UPLOADED,
        ),
        _event(
            T0 + 20 * minute,
            f"worker:{STUDIO_ID}",
            "state_changed",
            job_id=done,
            worker_id=STUDIO_ID,
            from_state=JobState.UPLOADED,
            to_state=JobState.DELIVERED,
        ),
        _event(
            T0 + 21 * minute,
            "system",
            "state_changed",
            job_id=done,
            from_state=JobState.DELIVERED,
            to_state=JobState.DONE,
        ),
        _event(
            T0 + 25 * minute,
            volunteer_actor,
            "lease_acquired",
            job_id=failed,
            worker_id=VOLUNTEER_ID,
            reason="process",
        ),
        _event(
            T0 + 30 * minute,
            volunteer_actor,
            "task_failed",
            job_id=failed,
            worker_id=VOLUNTEER_ID,
            reason="ffmpeg exited: moov atom not found",
        ),
        _event(
            T0 + 30 * minute,
            volunteer_actor,
            "state_changed",
            job_id=failed,
            worker_id=VOLUNTEER_ID,
            from_state=JobState.CLAIMED,
            to_state=JobState.FAILED,
            reason="process failed",
        ),
        _event(
            T0 + 35 * minute,
            "system",
            "lease_expired",
            job_id="job-other-9",
            worker_id=VOLUNTEER_ID,
            reason="no heartbeat for 45 s",
        ),
    ]
    # Ids count up from 1 in log order, as the orchestrator assigns them.
    return [Event(id=number, **entry) for number, entry in enumerate(entries, start=1)]
