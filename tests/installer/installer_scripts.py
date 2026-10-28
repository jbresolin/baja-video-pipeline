"""Load installer/build.py and installer/fetch_vendor.py as modules.

They are scripts, and "build" is a common module name, so instead of a
plain `import build` (which could pick up some other package called
"build") each one is loaded from its file under a unique name.
"""

import importlib.util
import sys
from pathlib import Path

INSTALLER_FOLDER = Path(__file__).resolve().parents[2] / "installer"


def load_installer_script(name: str):
    module_name = f"installer_script_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, INSTALLER_FOLDER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
