"""BajaWorker.exe's command line.

    BajaWorker.exe --version                 print the build's version
    BajaWorker.exe --check                   is the build complete? (exit 0 = yes)
    BajaWorker.exe --init-config PATH        installer: write its keys into worker.json
    BajaWorker.exe --first-run [--config P]  after install: join the Baja tailnet
    BajaWorker.exe --leave-tailnet           uninstaller: tailscale logout
    BajaWorker.exe --service --config P …    NSSM: wait for Tailscale, then run the worker
    BajaWorker.exe [--config P] [--console]  run the worker (= python -m worker)

Anything this module does not recognise is passed on to the worker's own
command line (worker/__main__.py).
"""

import argparse
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

from baja_installer import build_info, layout, windows
from baja_installer.check import run_check
from baja_installer.first_run import EXIT_CANCELLED, FirstRunEnvironment, run_first_run
from baja_installer.init_config import write_installer_config
from baja_installer.leave_tailnet import leave_baja_tailnet
from baja_installer.service_start import EXIT_TAILNET_NOT_READY, wait_for_tailnet_address
from baja_installer.tailscale_cli import TailscaleCli, find_tailscale_executable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="BajaWorker.exe", add_help=True)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("--version", action="store_true", help="print the version")
    command.add_argument("--check", action="store_true", help="verify this build")
    command.add_argument(
        "--init-config", type=Path, metavar="PATH", help="write the installer's config keys"
    )
    command.add_argument("--first-run", action="store_true", help="join the Baja tailnet")
    command.add_argument("--leave-tailnet", action="store_true", help="tailscale logout")
    command.add_argument("--service", action="store_true", help="run as the NSSM service")
    parser.add_argument("--config", type=Path, help="worker.json (default: C:\\BajaWorker)")
    return parser


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # harmless; required if anything ever forks
    args, worker_arguments = build_parser().parse_known_args(argv)
    config_path = (args.config or layout.default_config_path()).resolve()
    install = layout.install_folder()

    if args.version:
        print(build_info.VERSION)
        return 0
    if args.check:
        print(f"BajaWorker {build_info.VERSION}")
        return run_check(install, build_info.bundled_modules())
    if args.init_config:
        write_installer_config(
            args.init_config.resolve(),
            ffmpeg_path=layout.bundled_ffmpeg(install),
            enroll_url=build_info.DEFAULT_ENROLL_URL,
        )
        return 0
    if args.first_run:
        return first_run_command(config_path, install)
    if args.leave_tailnet:
        return leave_baja_tailnet(TailscaleCli(find_tailscale_executable()))
    if args.service and not wait_for_tailnet_address(config_path):
        return EXIT_TAILNET_NOT_READY
    return run_worker(config_path, worker_arguments)


def run_worker(config_path: Path, worker_arguments: list[str]) -> int:
    # Imported here so `--version` and `--check` work even if the worker
    # itself is broken (--check then says which module failed).
    from worker.__main__ import main as worker_main

    return worker_main(["--config", str(config_path), *worker_arguments])


def first_run_command(config_path: Path, install: Path) -> int:
    if os.name == "nt" and not windows.is_elevated():
        # Started from the Start menu: ask for admin rights once, in a new window.
        if windows.relaunch_elevated(["--first-run", "--config", str(config_path)]):
            return 0
        print("É preciso permitir o acesso de administrador para concluir a conexão.")
        return EXIT_CANCELLED

    def restart_service() -> None:
        try:
            windows.restart_service(install)
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Não consegui reiniciar o serviço ({error}). Reinicie o PC.")

    environment = FirstRunEnvironment(
        config_path=config_path,
        tailscale=TailscaleCli(find_tailscale_executable()),
        open_setup_page=lambda: windows.open_url_as_desktop_user(layout.SETUP_PAGE_URL),
        restart_service=restart_service,
        ask_yes_no=windows.ask_yes_no,
        say=print,
        sleep=time.sleep,
    )
    try:
        return run_first_run(environment)
    except KeyboardInterrupt:
        return EXIT_CANCELLED
    finally:
        if os.name == "nt":
            windows.wait_for_enter()


if __name__ == "__main__":
    sys.exit(main())
