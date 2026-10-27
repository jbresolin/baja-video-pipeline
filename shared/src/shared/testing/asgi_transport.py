"""Call a FastAPI/Starlette app from a normal (synchronous) httpx.Client, in memory.

    client = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://test")
    client.get("/workers")   # runs the app's handler directly, no network

The shared transfer helpers take an httpx.Client, so tests (and the worker
agent's tests against a fake orchestrator) can hand them a client like this.
httpx only ships an *async* ASGI transport; this wraps it for sync use.
"""

import asyncio

import httpx


class SyncAsgiTransport(httpx.BaseTransport):
    def __init__(self, app) -> None:
        self._async_transport = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # Each request runs in its own short-lived event loop. Fine for
        # tests; do not use this from code that is already async.
        return asyncio.run(self._forward(request))

    async def _forward(self, request: httpx.Request) -> httpx.Response:
        response = await self._async_transport.handle_async_request(request)
        body = await response.aread()
        return httpx.Response(response.status_code, headers=response.headers, content=body)
