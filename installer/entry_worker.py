"""PyInstaller entry script for BajaWorker.exe (see installer/build.py).

Everything lives in baja_installer.cli; this file only exists because
PyInstaller wants a script, not a module, as its starting point.
"""

import sys

from baja_installer.cli import main

if __name__ == "__main__":
    sys.exit(main())
