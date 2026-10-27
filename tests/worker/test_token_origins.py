"""Who may see the bearer token (design §17, docs/api.md §5 "Where the bearer token goes").

The token goes to exactly two origins: the worker's `orchestrator_url` and
the `tailnet_base_url` from /enroll. An origin is (scheme, host, port), so
the same machine on another port (a peer's :8710, the studio's :8720) is a
different origin and never gets it.
"""

import httpx
import pytest

from worker.api_client import OrchestratorClient
from worker.token_origins import may_send_bearer_token, url_origin

ORCHESTRATOR = "http://100.64.0.1:8080"
TAILNET = "http://100.64.0.1:8081"
TRUSTED = (ORCHESTRATOR, TAILNET)


def test_origin_is_scheme_host_and_port_with_default_ports_filled_in():
    assert url_origin("http://Caddy.Test/inbox/a.mp4") == ("http", "caddy.test", 80)
    assert url_origin("https://vps.example.org/enroll") == ("https", "vps.example.org", 443)
    assert url_origin("http://100.64.0.9:8710/raw/x") == ("http", "100.64.0.9", 8710)


@pytest.mark.parametrize("not_a_full_url", ["", "/inbox/a.mp4", "inbox/a.mp4", "http://host:abc/"])
def test_relative_or_broken_urls_have_no_origin(not_a_full_url):
    assert url_origin(not_a_full_url) is None


@pytest.mark.parametrize(
    "url",
    [
        f"{ORCHESTRATOR}/workers/w1/heartbeat",  # the API
        f"{TAILNET}/inbox/job.mp4",  # Caddy's inbox on the tailnet listener
        f"{TAILNET}/outbox/job.mp4",  # Caddy's outbox
        "HTTP://100.64.0.1:8080/jobs/x/clip",  # case does not change the origin
    ],
)
def test_the_two_trusted_origins_get_the_token(url):
    assert may_send_bearer_token(url, TRUSTED)


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.1:8710/raw/job",  # same machine, peer file server port
        "http://100.64.0.9:8720/receive/job",  # the studio
        "https://100.64.0.1:8080/inbox/job.mp4",  # https is another origin
        "http://100.64.0.1/inbox/job.mp4",  # port 80 is another origin
        "http://evil.example/inbox/job.mp4",  # anything else an offer might carry
        "/inbox/job.mp4",  # not absolute: we cannot tell, so no
    ],
)
def test_every_other_url_gets_no_token(url):
    assert not may_send_bearer_token(url, TRUSTED)


def test_old_configs_without_a_tailnet_base_url_trust_only_the_orchestrator():
    # worker.json files written before tailnet_base_url existed keep
    # working: None is skipped, the orchestrator_url still counts.
    assert may_send_bearer_token(f"{ORCHESTRATOR}/jobs/x", (ORCHESTRATOR, None))
    assert not may_send_bearer_token(f"{TAILNET}/inbox/x.mp4", (ORCHESTRATOR, None))


def test_the_api_client_applies_the_rule_to_every_url():
    # The client is what tasks ask for headers, so it must follow the
    # same rule: the token for trusted origins, nothing for the rest.
    client = OrchestratorClient(
        httpx.Client(), ORCHESTRATOR, bearer_token="tok", tailnet_base_url=TAILNET
    )
    assert client.auth_headers_for(f"{TAILNET}/inbox/a.mp4") == {"Authorization": "Bearer tok"}
    assert client.auth_headers_for("/jobs/x/proxy") == {"Authorization": "Bearer tok"}
    assert client.auth_headers_for("http://100.64.0.9:8720/receive/x") == {}
    # Upload headers always carry the lease id; the token only when allowed.
    assert client.upload_headers("http://elsewhere.test/clip", "lease-1") == {"Lease-Id": "lease-1"}


def test_api_calls_to_a_foreign_absolute_url_do_not_leak_the_token():
    # Offers carry absolute upload URLs (e.g. the thumbnail URL). If one
    # ever pointed somewhere else, the API client must not attach the token.
    seen_headers = []

    def record(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        return httpx.Response(204)

    client = OrchestratorClient(
        httpx.Client(transport=httpx.MockTransport(record)),
        ORCHESTRATOR,
        bearer_token="tok",
        tailnet_base_url=TAILNET,
    )
    client.upload_thumbnail("http://elsewhere.test/jobs/x/thumbnail", "lease-1", b"jpeg")
    client.upload_thumbnail(f"{ORCHESTRATOR}/jobs/x/thumbnail", "lease-1", b"jpeg")

    assert "authorization" not in seen_headers[0]
    assert seen_headers[1]["authorization"] == "Bearer tok"
