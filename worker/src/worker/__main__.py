"""`python -m worker [--config worker.json] [--console]`: the worker daemon.

Built to run unattended as a Windows service (NSSM) or a Linux service:
no interactive input, settings from the config file, logs to
<working folder>/logs/worker.log. It stops cleanly on CTRL-C, SIGTERM and
(Windows) CTRL-BREAK, which is how NSSM asks a console program to stop.

Startup:
 1. start the localhost setup/status page and its JSON API, first and
    unconditionally (even with a broken worker.json), so a volunteer can
    always see what is wrong;
 2. worker.lifecycle: fix worker.json if needed, enroll, (studio) choose
    the clip folder, wait for the tailnet address, then run the daemon for
    the enrolled role until asked to stop.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import httpx

from worker.config import CONFIG_FILE_NAME, DEFAULT_SETUP_PAGE_PORT, WorkerConfig
from worker.config_problems import read_config
from worker.daemon import Daemon, build_api_client
from worker.enrollment import detect_capabilities
from worker.lifecycle import WorkerLifecycle
from worker.logging_setup import configure_logging
from worker.media_tools import MediaTools
from worker.pause import PauseSwitch
from worker.servers import BackgroundServer
from worker.setup_page import build_setup_app
from worker.status import StatusBoard
from worker.storage import LOGS_FOLDER

logger = logging.getLogger("worker")


def install_stop_signal_handlers(shutdown: threading.Event) -> None:
    def request_stop(signal_number, _frame) -> None:
        logger.info("received signal %s; stopping", signal_number)
        shutdown.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)


WINDOWS_SERVICE_NAME = "BajaWorker"


def stop_service_action(shutdown: threading.Event):
    """What "Desligar o worker" on the status page does.

    On Windows, ask the service manager to stop the BajaWorker service:
    simply exiting would make NSSM start us again 10 s later. Anywhere else
    (or when we are not that service), stop this process.
    """

    def stop() -> None:
        def run() -> None:
            if os.name == "nt":
                finished = subprocess.run(
                    ["sc.exe", "stop", WINDOWS_SERVICE_NAME],
                    capture_output=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if finished.returncode == 0:
                    return  # the service manager will stop us (CTRL-C via NSSM)
            logger.info("stop requested from the status page")
            shutdown.set()

        # In the background: the page's answer must not wait for the stop.
        threading.Thread(target=run, name="stop-service", daemon=True).start()

    return stop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m worker", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--config", default=CONFIG_FILE_NAME, help="config file (default: ./worker.json)"
    )
    parser.add_argument("--console", action="store_true", help="also log to the terminal")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    # A broken worker.json must not stop us here: log next to it until it is fixed.
    reading = read_config(config_path)
    log_folder = (
        reading.config.resolved_working_folder(config_path)
        if reading.config is not None
        else config_path.parent
    ) / LOGS_FOLDER
    configure_logging(log_folder, also_to_console=args.console)
    logger.info("worker starting with config %s", config_path)

    shutdown = threading.Event()
    install_stop_signal_handlers(shutdown)
    status = StatusBoard()
    setup_submitted = threading.Event()
    # One pause switch for the page/tray API and the daemon (worker.pause).
    pause = PauseSwitch(config_path)

    def start_daemon(config: WorkerConfig, stop: threading.Event) -> Daemon:
        daemon = Daemon(
            config,
            config_path,
            status,
            stop,
            media=_media_tools_for(config),
            transport=httpx.HTTPTransport(),
            pause=pause,
        )
        daemon.start()
        return daemon

    def use_log_folder_of(config: WorkerConfig) -> None:
        nonlocal log_folder
        new_log_folder = config.resolved_working_folder(config_path) / LOGS_FOLDER
        if new_log_folder != log_folder:  # the setup page chose another working folder
            configure_logging(new_log_folder, also_to_console=args.console)
            log_folder = new_log_folder

    enrollment_transport = httpx.HTTPTransport()
    lifecycle = WorkerLifecycle(
        config_path,
        status,
        shutdown,
        setup_submitted=setup_submitted,
        start_daemon=start_daemon,
        enroll_api_for=lambda current: build_api_client(current, enrollment_transport),
        capabilities_for=lambda current: detect_capabilities(current.ffmpeg_path),
        on_enrolled=use_log_folder_of,
    )
    page_port = reading.config.setup_page_port if reading.config else DEFAULT_SETUP_PAGE_PORT
    setup_server = BackgroundServer(
        build_setup_app(
            config_path,
            status,
            setup_submitted,
            page_port,
            pause=pause,
            stop_service=stop_service_action(shutdown),
            re_register=lifecycle.re_register,
        ),
        "127.0.0.1",
        page_port,
        "setup-page",
    )
    setup_server.start()
    lifecycle.run()
    setup_server.stop()
    return 0


def _media_tools_for(config: WorkerConfig) -> MediaTools:
    encoder = detect_capabilities(config.ffmpeg_path).video_encoder
    logger.info("video encoder: %s", encoder)
    return MediaTools(config.ffmpeg_path, config.resolved_ffprobe_path(), encoder)


if __name__ == "__main__":
    sys.exit(main())
