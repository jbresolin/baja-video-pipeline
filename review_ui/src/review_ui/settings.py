"""Settings for the review UI, read from BVP_* environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

# How often the pages ask the server for fresh data, in seconds.
QUEUE_POLL_SECONDS = 5
JOB_PANEL_POLL_SECONDS = 5
FLEET_POLL_SECONDS = 10


@dataclass(frozen=True)
class ReviewUiSettings:
    # Where the orchestrator API lives. In compose: http://orchestrator:8000.
    orchestrator_url: str = "http://127.0.0.1:8000"
    # The public 443 site, e.g. "https://baja.example.org". Proxy and
    # thumbnail paths from the orchestrator are relative to it. Empty means
    # "same site as the UI", which is what local development wants.
    public_base_url: str = ""
    port: int = 8001
    # How long one orchestrator call may take before the page shows an error.
    orchestrator_timeout_s: float = 10.0
    # Optional clip root on the studio box, e.g. "D:\\Baja\\clips". When set,
    # the clips page copies full paths instead of paths relative to it.
    studio_clip_root: str = ""

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "ReviewUiSettings":
        env = os.environ if environment is None else environment
        defaults = cls()
        return cls(
            orchestrator_url=env.get("BVP_ORCHESTRATOR_URL", defaults.orchestrator_url),
            public_base_url=env.get("BVP_PUBLIC_BASE_URL", defaults.public_base_url).rstrip("/"),
            port=int(env.get("BVP_REVIEW_UI_PORT", defaults.port)),
            orchestrator_timeout_s=float(
                env.get("BVP_ORCHESTRATOR_TIMEOUT_S", defaults.orchestrator_timeout_s)
            ),
            studio_clip_root=env.get("BVP_STUDIO_CLIP_ROOT", defaults.studio_clip_root),
        )
