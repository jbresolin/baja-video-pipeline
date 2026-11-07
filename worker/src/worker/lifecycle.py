"""The worker's startup sequence, run after the status page is already up.

worker.__main__ starts the localhost status page and its JSON API first,
unconditionally, then hands over to WorkerLifecycle.run(), which walks
through these steps, each one a visible phase (worker.status.Phase) on the
page and in the tray:

  1. CONFIG_PROBLEM  worker.json does not parse or lost a field: show the
                     file, line, column and a hint, wait for the fix;
  2. SETUP           not enrolled: wait for the token form, POST /enroll;
  3. STUDIO_FOLDER   a studio without a clip folder: wait for the second
                     form (worker.studio.clip_folder);
  4. WAITING_FOR_TAILNET  the saved tailnet address is not on this PC yet
                     (worker.tailnet_wait);
  5. RUNNING         the daemon, until the service stops or the volunteer
                     presses "Registrar de novo".

"Registrar de novo" clears only the identity in worker.json
(worker.config.config_without_identity), stops the daemon and starts
again at step 1, which then shows the token form: the process and the
status page stay up throughout, NSSM never sees an exit.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from shared.models import WorkerRole
from worker.config import WorkerConfig, config_without_identity, save_config
from worker.config_problems import ConfigProblemError, read_config, require_usable_config
from worker.enrollment import TAILSCALE_AUTH_KEY_FILE_NAME, wait_until_enrolled
from worker.status import Phase, StatusBoard
from worker.studio.clip_folder import prepare_clip_root
from worker.tailnet_wait import TAILNET_POLL_SECONDS, tailnet_problem_for, wait_for_tailnet, waiter

logger = logging.getLogger(__name__)

CONFIG_POLL_SECONDS = 2.0
# After the daemon could not even be built (e.g. the working folder is on a
# drive that is not plugged in), try again this much later.
DAEMON_START_RETRY_SECONDS = 30.0
STATUS_KEY_STUDIO_FOLDER = "studio folder"


class RunningDaemon(Protocol):
    def stop(self) -> None: ...


# Builds and starts the daemon for an enrolled config. Its argument `stop`
# is the event the daemon's threads watch; the lifecycle sets it through
# daemon.stop().
DaemonStarter = Callable[[WorkerConfig, threading.Event], RunningDaemon]


class WorkerLifecycle:
    def __init__(
        self,
        config_path: Path,
        status: StatusBoard,
        shutdown: threading.Event,
        *,
        setup_submitted: threading.Event,
        start_daemon: DaemonStarter,
        enroll_api_for,
        capabilities_for,
        check_tailnet=tailnet_problem_for,
        prepare_studio_folder: Callable[[Path], str | None] = prepare_clip_root,
        on_enrolled: Callable[[WorkerConfig], None] = lambda config: None,
        config_poll_seconds: float = CONFIG_POLL_SECONDS,
        tailnet_poll_seconds: float = TAILNET_POLL_SECONDS,
        daemon_retry_seconds: float = DAEMON_START_RETRY_SECONDS,
    ) -> None:
        """`enroll_api_for` / `capabilities_for`: see enrollment.wait_until_enrolled.
        `check_tailnet(address)`: worker.tailnet_wait.tailnet_problem_for (tests
        pass a fake). `on_enrolled(config)`: called before each daemon start
        (worker.__main__ moves the log file to the chosen working folder)."""
        self.config_path = config_path
        self.status = status
        self.shutdown = shutdown
        self.setup_submitted = setup_submitted
        self._start_daemon = start_daemon
        self._enroll_api_for = enroll_api_for
        self._capabilities_for = capabilities_for
        self._check_tailnet = check_tailnet
        self._prepare_studio_folder = prepare_studio_folder
        self._on_enrolled = on_enrolled
        self._config_poll_seconds = config_poll_seconds
        self._tailnet_poll_seconds = tailnet_poll_seconds
        self._daemon_retry_seconds = daemon_retry_seconds
        # Set by "Registrar de novo"; ends the current step and starts over.
        self.restart_requested = threading.Event()
        self._should_stop = waiter(shutdown, self.restart_requested)

    # --- Actions from the status page / tray (worker.local_api) ---

    def re_register(self) -> bool:
        """ "Registrar de novo": forget the identity and go back to the token form.

        False when worker.json does not parse (nothing safe to rewrite; the
        page shows the syntax problem instead). raws/ and clips/ stay.
        """
        reading = read_config(self.config_path)
        if reading.config is None:
            return False
        save_config(self.config_path, config_without_identity(reading.config))
        # A key from the old enrollment is spent or useless now.
        (self.config_path.parent / TAILSCALE_AUTH_KEY_FILE_NAME).unlink(missing_ok=True)
        logger.warning("identity cleared by the volunteer (Registrar de novo); back to setup")
        self.status.forget_identity_facts()
        self.restart_requested.set()
        self.setup_submitted.set()  # wake a step that waits for the form
        return True

    # --- The sequence ---

    def run(self) -> None:
        """Until shutdown: steps 1–5, again from the top after "Registrar de novo"."""
        while not self.shutdown.is_set():
            self.restart_requested.clear()
            try:
                config = self._ready_config()
            except ConfigProblemError:
                continue  # broken again mid-way: step 1 shows it
            if config is None:
                continue  # shutdown or restart; the loop condition decides
            self._run_daemon(config)

    def _ready_config(self) -> WorkerConfig | None:
        """Steps 1–4. None when asked to stop or restart on the way."""
        if self._wait_for_usable_config() is None:
            return None
        config = wait_until_enrolled(
            self.config_path,
            api_for=self._enroll_api_for,
            capabilities_for=self._capabilities_for,
            status=self.status,
            setup_submitted=self.setup_submitted,
            shutdown=self.shutdown,
        )
        if config is None:
            return None
        if config.role == WorkerRole.STUDIO:
            config = self._wait_for_studio_folder()
            if config is None:
                return None
        return wait_for_tailnet(
            self.config_path,
            self.status,
            self._should_stop,
            check=self._check_tailnet,
            poll_seconds=self._tailnet_poll_seconds,
        )

    def _wait_for_usable_config(self) -> WorkerConfig | None:
        logged_message = None
        while True:
            reading = read_config(self.config_path)
            if reading.usable:
                return reading.config
            problem = reading.problem
            assert problem is not None
            self.status.set_phase(Phase.CONFIG_PROBLEM)
            if problem.message != logged_message:
                logger.error(
                    "worker.json has a problem; waiting for a fix: %s (line %s, column %s)",
                    problem.message,
                    problem.line,
                    problem.column,
                )
                logged_message = problem.message
            if self._should_stop(self._config_poll_seconds):
                return None

    def _wait_for_studio_folder(self) -> WorkerConfig | None:
        """A studio needs its clip folder before the receiver starts (item 5)."""
        while True:
            config = require_usable_config(self.config_path)
            if config.studio_clip_root is not None:
                problem = self._prepare_studio_folder(config.studio_clip_root)
                self.status.set(STATUS_KEY_STUDIO_FOLDER, problem or "ok")
                return config
            self.status.set_phase(Phase.STUDIO_FOLDER)
            if self.setup_submitted.wait(min(1.0, self._config_poll_seconds)):
                self.setup_submitted.clear()
            if self._should_stop(0):
                return None

    def _run_daemon(self, config: WorkerConfig) -> None:
        self._on_enrolled(config)
        stop = threading.Event()
        try:
            daemon = self._start_daemon(config, stop)
        except Exception:
            # E.g. the working folder's drive is missing. Shown in "Detalhes";
            # the page stays up and we try again.
            logger.exception("the worker could not start")
            self.status.record_error("o worker não conseguiu iniciar; veja logs/worker.log")
            self._should_stop(self._daemon_retry_seconds)
            return
        self.status.set_phase(Phase.RUNNING)
        # Also leave when the daemon set its own stop event: it asked for a
        # fresh start (e.g. the orchestrator changed its role). The loop in
        # run() then reads worker.json again and starts a new daemon.
        while not self._should_stop(1.0) and not stop.is_set():
            pass
        if stop.is_set() and not self.shutdown.is_set():
            logger.info("the daemon stopped itself; starting again from worker.json")
        daemon.stop()
        if not self.shutdown.is_set():
            self.status.set_phase(Phase.STARTING)
