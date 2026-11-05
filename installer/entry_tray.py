"""PyInstaller entry script for BajaWorkerTray.exe, the notification-area icon.

Frozen by installer/build.py next to BajaWorker.exe, as a windowed program
(no console window at logon). Everything lives in worker.tray.app.
"""

import sys

from worker.tray.app import run_tray

if __name__ == "__main__":
    sys.exit(run_tray())
