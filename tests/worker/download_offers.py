"""Helpers shared by the download_raw tests: the offer, and a slow inbox."""

import httpx

from shared.models import DownloadRawTask
from tests.worker.conftest import PEER_URL
from tests.worker.fake_orchestrator import BASE_URL


def download_task(job, *, inbox: bool = True, fallbacks=()) -> DownloadRawTask:
    """The offer the orchestrator would send for `job`.

    `inbox=False` makes the peer at peer.test:8710 the main source, as for
    a master that is offered a job after the VPS deleted its copy.
    """
    main_url = f"{BASE_URL}/inbox/{job.job_id}.mp4" if inbox else f"{PEER_URL}/raw/{job.job_id}"
    return DownloadRawTask(
        job_id=job.job_id,
        sha256=job.sha256,
        size_bytes=len(job.raw_bytes),
        download_url=main_url,
        fallback_download_urls=list(fallbacks),
    )


def streaming_inbox(content: bytes, after_first_piece) -> httpx.MockTransport:
    """An inbox that sends the file in 10 KB pieces and calls `after_first_piece`
    once the first piece is out, to simulate something happening mid-download."""

    def handler(request: httpx.Request) -> httpx.Response:
        def pieces():
            for start in range(0, len(content), 10_000):
                yield content[start : start + 10_000]
                if start == 0:
                    after_first_piece()

        return httpx.Response(200, content=pieces())

    return httpx.MockTransport(handler)
