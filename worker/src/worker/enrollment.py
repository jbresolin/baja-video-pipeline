"""First run: exchange the one-time enrollment token for our identity.

design §8, docs/api.md §5: POST /enroll with the token and a description of
this machine returns a worker id, a bearer token, our role and a Tailscale
auth key. We save the first three in the config file (and forget the
enrollment token, which is now used up), and write the auth key to
`tailscale-authkey.txt` next to the config file. The installer reads that
file and runs `tailscale up --authkey=…`; the daemon itself never runs
Tailscale.
"""

import logging
import os
import platform
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from shared.ffmpeg import NVENC_ENCODER, X264_ENCODER, FfmpegNotFoundError, detect_encoder
from shared.models import EnrollRequest, WorkerCapabilities
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.config import WorkerConfig, load_config, save_config, write_text_atomically
from worker.status import StatusBoard

logger = logging.getLogger(__name__)

TAILSCALE_AUTH_KEY_FILE_NAME = "tailscale-authkey.txt"
ENROLL_RETRY_SECONDS = 30.0
COMMAND_TIMEOUT_SECONDS = 20


class EnrollmentRefusedError(Exception):
    """The token is unknown, used or expired: the volunteer needs a new one."""


def _run_quietly(args: list[str]) -> str | None:
    """stdout of a small helper command, or None if it is missing or fails."""
    try:
        finished = subprocess.run(
            args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return finished.stdout.strip() if finished.returncode == 0 else None


def detect_capabilities(ffmpeg_path: str) -> WorkerCapabilities:
    """Describe this machine for /enroll (NVENC probe as in design §9)."""
    try:
        encoder = detect_encoder(ffmpeg_path)
    except FfmpegNotFoundError:
        logger.error("ffmpeg not found at '%s'; rendering will fail", ffmpeg_path)
        encoder = X264_ENCODER
    version_output = _run_quietly([ffmpeg_path, "-hide_banner", "-version"])
    gpu_output = _run_quietly(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    return WorkerCapabilities(
        has_nvenc=encoder == NVENC_ENCODER,
        video_encoder=encoder,
        ffmpeg_version=version_output.splitlines()[0] if version_output else None,
        os=platform.platform(),
        cpu_count=os.cpu_count(),
        gpu_name=gpu_output.splitlines()[0] if gpu_output else None,
    )


def detect_tailnet_address() -> str | None:
    """Our Tailscale IPv4 address, if Tailscale is installed and up."""
    output = _run_quietly(["tailscale", "ip", "-4"])
    return output.splitlines()[0] if output else None


def enroll(
    config: WorkerConfig,
    config_path: Path,
    api: OrchestratorClient,
    capabilities: WorkerCapabilities,
) -> WorkerConfig:
    """Do the exchange and persist the result. Returns the updated config."""
    request = EnrollRequest(
        enrollment_token=config.enrollment_token or "",
        worker_name=config.worker_name,
        hostname=platform.node() or None,
        capabilities=capabilities,
        storage_cap_bytes=config.storage_cap_bytes,
    )
    try:
        answer = api.enroll(config.resolved_enroll_url(), request)
    except ApiError as error:
        if error.code in ("enrollment_token_invalid", "validation_error"):
            raise EnrollmentRefusedError(str(error)) from error
        raise

    # The key first: if we crashed after saving the config but before this,
    # the key would be lost and the (used) token could not get another one.
    key_path = config_path.parent / TAILSCALE_AUTH_KEY_FILE_NAME
    write_text_atomically(key_path, answer.tailscale_auth_key + "\n")
    enrolled = config.model_copy(
        update={
            "enrollment_token": None,
            "worker_id": answer.worker_id,
            "bearer_token": answer.bearer_token,
            "role": answer.role,
        }
    )
    save_config(config_path, enrolled)
    logger.info("enrolled as %s (role %s); Tailscale key written", answer.worker_id, answer.role)
    return enrolled


def wait_until_enrolled(
    config_path: Path,
    api_for: Callable[[WorkerConfig], OrchestratorClient],
    capabilities_for: Callable[[WorkerConfig], WorkerCapabilities],
    status: StatusBoard,
    setup_submitted: threading.Event,
    shutdown: threading.Event,
    retry_seconds: float = ENROLL_RETRY_SECONDS,
) -> WorkerConfig | None:
    """Block until the config says we are enrolled; None if shutdown came first.

    `api_for(config)` builds an OrchestratorClient for that config;
    `capabilities_for(config)` describes the machine. Both are parameters
    so tests can pass fakes.
    """
    while not shutdown.is_set():
        config = load_config(config_path)  # the setup page may have changed it
        if config.is_enrolled:
            return config
        if not config.enrollment_token:
            status.set("setup", "waiting for the enrollment token (open the setup page)")
            setup_submitted.wait(1.0)
            setup_submitted.clear()
            continue
        status.set("setup", "enrolling…")
        try:
            return enroll(config, config_path, api_for(config), capabilities_for(config))
        except EnrollmentRefusedError as refused:
            logger.error("enrollment refused: %s", refused)
            status.set("setup", f"the enrollment token was refused ({refused}); enter a new one")
            save_config(config_path, config.model_copy(update={"enrollment_token": None}))
        except (OrchestratorUnreachableError, ApiError) as error:
            logger.warning("enrollment failed, will retry: %s", error)
            status.set("setup", f"cannot enroll yet ({error}); retrying")
            shutdown.wait(retry_seconds)
    return None
