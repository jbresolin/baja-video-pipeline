"""Shared pytest fixtures for the orchestrator tests.

A "fixture" is something pytest builds for a test when the test names it
as a parameter. Here, any test that takes `harness` gets a brand-new
orchestrator with an empty database in its own temporary folder, wired to
fake WhatsApp/Evolution/Tailscale/Baja-app services. Nothing is shared
between tests and nothing touches the network.
"""

import pytest

from orchestrator.testing.harness import OrchestratorHarness, build_harness


@pytest.fixture
def harness(tmp_path) -> OrchestratorHarness:
    # tmp_path is pytest's own fixture: an empty folder unique to this test.
    built = build_harness(tmp_path / "data")
    yield built
    built.client.close()
    built.context.database.close()
