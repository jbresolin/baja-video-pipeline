"""Setup shared by the installer tests.

pytest runs this file before any test in this folder. The code under test
lives in installer/ (not an installed package): `baja_installer` is the
package frozen into BajaWorker.exe, and build.py / fetch_vendor.py are
scripts run by CI. Putting installer/ on sys.path lets the tests import
them exactly the way the scripts and the frozen exe do.
"""

import sys
from pathlib import Path

INSTALLER_FOLDER = Path(__file__).resolve().parents[2] / "installer"

if str(INSTALLER_FOLDER) not in sys.path:
    sys.path.insert(0, str(INSTALLER_FOLDER))
