"""Everything a request handler or background task needs, in one object.

Built once by app.create_app() and stored on app.state. Tests build it
with fakes (a ManualClock, httpx clients on MockTransports, a temp data
dir), which is how the whole service runs without a network.
"""

import threading
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from orchestrator.clients.evolution import EvolutionClient
from orchestrator.clients.tailscale import TailscaleClient
from orchestrator.clients.teams import TeamDirectory
from orchestrator.clock import Clock
from orchestrator.config import Settings
from orchestrator.db.database import Database
from orchestrator.security import ReviewerDirectory, SlidingWindowRateLimiter
from orchestrator.storage import VpsLayout


class KeyedLocks:
    """One lock per key (e.g. per upload file), created on first use."""

    def __init__(self) -> None:
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks[key]


@dataclass
class OrchestratorContext:
    settings: Settings
    database: Database
    clock: Clock
    layout: VpsLayout
    reviewers: ReviewerDirectory
    # Downloads WhatsApp media from the CDN (only URLs under BVP_WHATSAPP_MEDIA_BASE_URL).
    media_http_client: httpx.Client
    evolution: EvolutionClient
    tailscale: TailscaleClient
    # Team names: BVP_TEAMS_FILE, then the Baja app (design §17).
    teams: TeamDirectory
    enroll_rate_limiter: SlidingWindowRateLimiter
    upload_locks: KeyedLocks = field(default_factory=KeyedLocks)
