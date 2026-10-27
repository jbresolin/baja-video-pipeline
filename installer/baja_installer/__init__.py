"""Windows packaging of the worker: everything `BajaWorker.exe` adds on top
of `python -m worker`.

Modules:
    cli            entry point: picks one of the commands below or runs the worker
    build_info     version, enroll URL and module list stamped by installer/build.py
    bundled_modules  which modules the frozen exe must contain
    layout         install folder layout (ffmpeg, nssm), service name, default paths
    check          `--check`: imports everything, probes the bundled ffmpeg
    init_config    `--init-config`: the installer's part of worker.json
    service_start  `--service`: wait for the Tailscale address, then run the worker
    tailscale_cli  runs and parses the `tailscale` command
    first_run      `--first-run`: setup page, `tailscale up`, service restart
    leave_tailnet  `--leave-tailnet`: `tailscale logout` from the uninstaller
    windows        elevation, opening the browser, restarting the service
"""
