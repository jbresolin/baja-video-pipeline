"""An httpx transport that stops a transfer as soon as its task is cancelled.

The shared transfer helpers (download_file, upload_file_in_chunks) take an
httpx.Client and run until done. To stop one early (lease lost, shutdown)
each task gets its own client whose transport checks the task's
CancelToken before every request and between every received piece of a
response body. The resulting TaskCancelledError propagates out of the helper:

  * a download stops mid-stream; what arrived is already in the ".part"
    file, so a later attempt can resume it;
  * an upload stops between chunks; the receiver keeps what it got.
"""

from collections.abc import Iterator

import httpx

from worker.leases import CancelToken

# Generous: an 8 MiB chunk over a slow volunteer uplink takes a while.
TRANSFER_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class _CancellableStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.SyncByteStream, cancel: CancelToken) -> None:
        self._inner = inner
        self._cancel = cancel

    def __iter__(self) -> Iterator[bytes]:
        for piece in self._inner:
            self._cancel.raise_if_cancelled()
            yield piece

    def close(self) -> None:
        self._inner.close()


class CancellableTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, cancel: CancelToken) -> None:
        # `inner` is shared by every task (connection pool); it is owned by
        # the daemon, so close() below deliberately leaves it open.
        self._inner = inner
        self._cancel = cancel

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._cancel.raise_if_cancelled()
        response = self._inner.handle_request(request)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CancellableStream(response.stream, self._cancel),
            extensions=response.extensions,
        )

    def close(self) -> None:
        pass


def transfer_client_for(inner: httpx.BaseTransport, cancel: CancelToken) -> httpx.Client:
    """A client for one task's downloads and uploads."""
    return httpx.Client(transport=CancellableTransport(inner, cancel), timeout=TRANSFER_TIMEOUT)
