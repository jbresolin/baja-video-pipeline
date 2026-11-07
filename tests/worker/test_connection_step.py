"""Item 6 of LIVESTACK-38: a worker that enrolled but has not joined the tailnet yet.

What happened on the first real install: the service enrolled (it got its
worker id and token), but `BajaWorker.exe --first-run` had not yet put the
PC on the tailnet. worker.json still had the built-in orchestrator_url
(http://127.0.0.1:8000), so every heartbeat failed with "ConnectError …
actively refused", and the status page showed exactly that: meaningless to
a volunteer. Now:
  * the status page says, in Portuguese, which Start-menu entry to open;
  * the service uses tailnet_base_url (from /enroll) as its API address
    while orchestrator_url is still the default, so once the join is done
    it works even if worker.json was not rewritten.
"""

from shared.models import WorkerRole
from tests.worker.conftest import build_harness
from tests.worker.fake_orchestrator import BASE_URL, TAILNET_URL
from tests.worker.fakes import RoutingTransport
from worker.config import (
    CONNECTION_STEP_PENDING_MESSAGE,
    DEFAULT_ORCHESTRATOR_URL,
    WorkerConfig,
)
from worker.daemon import build_api_client
from worker.heartbeat import Heartbeater

TAILNET = "http://100.64.0.1:8080"


def enrolled_config(**fields) -> WorkerConfig:
    """A config as it looks right after a successful POST /enroll."""
    return WorkerConfig(worker_id="w-1", bearer_token="secret", role=WorkerRole.WORKER, **fields)


def test_enrolled_on_the_default_url_means_the_connection_step_is_pending():
    # Enrolled, default URL: the join step has not rewritten worker.json yet.
    assert enrolled_config().connection_step_pending
    # After --first-run the URL is Caddy's tailnet listener: nothing pending.
    assert not enrolled_config(orchestrator_url=TAILNET).connection_step_pending
    # Not enrolled at all: that is the setup form's job, not this message's.
    assert not WorkerConfig().connection_step_pending


def test_the_service_uses_tailnet_base_url_while_the_url_is_the_default():
    # /enroll told us Caddy's tailnet listener; the default URL is useless on
    # an installed PC, so API calls go to the tailnet listener instead.
    config = enrolled_config(tailnet_base_url=TAILNET)
    assert config.resolved_orchestrator_url() == TAILNET
    # A URL someone set on purpose is always kept.
    config = enrolled_config(orchestrator_url="http://other:9000", tailnet_base_url=TAILNET)
    assert config.resolved_orchestrator_url() == "http://other:9000"
    # No tailnet_base_url (an older enrollment): stay on the default.
    assert enrolled_config().resolved_orchestrator_url() == DEFAULT_ORCHESTRATOR_URL


def test_the_daemons_api_client_talks_to_the_resolved_url():
    # build_api_client is what the daemon uses; check it picks the same URL.
    client = build_api_client(enrolled_config(tailnet_base_url=TAILNET), RoutingTransport())
    assert client.base_url == TAILNET


def test_a_failed_heartbeat_shows_the_portuguese_hint_while_the_step_is_pending(tmp_path):
    # Set-up: the usual worker test harness, but with a network where the
    # orchestrator does not answer at all (RoutingTransport raises
    # ConnectError for unknown hosts, like a refused connection), and a
    # heartbeater told the connection step is pending.
    harness = build_harness(tmp_path)
    offline_api = build_api_client(enrolled_config(), RoutingTransport())
    heartbeater = Heartbeater(
        api=offline_api,
        role=WorkerRole.WORKER,
        storage=harness.storage,
        leases=harness.leases,
        measurements=harness.env.measurements,
        status=harness.status,
        tailnet_address=None,
        file_server_port=8710,
        receiver_port=8720,
        connection_step_pending=True,
    )

    assert heartbeater.beat_once() is None

    values, _ = harness.status.snapshot()
    # The volunteer reads what to do, not "ConnectError"…
    assert values["orchestrator"] == CONNECTION_STEP_PENDING_MESSAGE
    # …while the technical error is still kept for the "Detalhes" section
    # ("Último erro", which reads it from the board's facts).
    assert "offline" in harness.status.facts().last_error


def test_without_the_pending_step_the_error_is_shown_as_before(tmp_path):
    # A worker on the tailnet whose orchestrator is down: a real outage, so
    # the plain "unreachable" text is right (no Start-menu hint).
    harness = build_harness(tmp_path)
    harness.network.remove(BASE_URL)
    harness.network.remove(TAILNET_URL)
    assert harness.heartbeater.beat_once() is None
    values, _ = harness.status.snapshot()
    assert values["orchestrator"].startswith("unreachable")
