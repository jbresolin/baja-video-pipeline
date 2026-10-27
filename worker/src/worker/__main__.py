"""`python -m worker [--config worker.json] [--console]`: the worker daemon.

Built to run unattended as a Windows service (NSSM) or a Linux service:
no interactive input, settings from the config file, logs to
<working folder>/logs/worker.log. It stops cleanly on CTRL-C, SIGTERM and
(Windows) CTRL-BREAK, which is how NSSM asks a console program to stop.

Startup:
 1. read the config; start the localhost setup/status page;
 2. if not enrolled, wait for the setup page, then POST /enroll;
 3. run the daemon for the enrolled role until asked to stop.
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

import httpx

from worker.config import CONFIG_FILE_NAME, WorkerConfig, load_config
from worker.daemon import Daemon, build_api_client
from worker.enrollment import detect_capabilities, wait_until_enrolled
from worker.logging_setup import configure_logging
from worker.media_tools import MediaTools
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m worker", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--config", default=CONFIG_FILE_NAME, help="config file (default: ./worker.json)"
    )
    parser.add_argument("--console", action="store_true", help="also log to the terminal")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    log_folder = config.resolved_working_folder(config_path) / LOGS_FOLDER
    configure_logging(log_folder, also_to_console=args.console)
    logger.info("worker starting with config %s", config_path)

    shutdown = threading.Event()
    install_stop_signal_handlers(shutdown)
    status = StatusBoard()
    setup_submitted = threading.Event()
    setup_server = BackgroundServer(
        build_setup_app(config_path, status, setup_submitted, config.setup_page_port),
        "127.0.0.1",
        config.setup_page_port,
        "setup-page",
    )
    setup_server.start()

    transport = httpx.HTTPTransport()
    enrolled = wait_until_enrolled(
        config_path,
        api_for=lambda current: build_api_client(current, transport),
        capabilities_for=lambda current: detect_capabilities(current.ffmpeg_path),
        status=status,
        setup_submitted=setup_submitted,
        shutdown=shutdown,
    )
    if enrolled is None:
        setup_server.stop()
        return 0

    new_log_folder = enrolled.resolved_working_folder(config_path) / LOGS_FOLDER
    if new_log_folder != log_folder:  # the setup page chose another working folder
        configure_logging(new_log_folder, also_to_console=args.console)

    daemon = Daemon(
        enrolled,
        config_path,
        status,
        shutdown,
        media=_media_tools_for(enrolled),
        transport=transport,
    )
    daemon.start()
    # Wait in short steps: on Windows a bare Event.wait() would delay CTRL-C.
    while not shutdown.wait(1.0):
        pass
    daemon.stop()
    setup_server.stop()
    return 0


def _media_tools_for(config: WorkerConfig) -> MediaTools:
    encoder = detect_capabilities(config.ffmpeg_path).video_encoder
    logger.info("video encoder: %s", encoder)
    return MediaTools(config.ffmpeg_path, config.resolved_ffprobe_path(), encoder)


if __name__ == "__main__":
    sys.exit(main())
