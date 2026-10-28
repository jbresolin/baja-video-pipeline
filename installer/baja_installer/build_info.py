"""What installer/build.py stamped into this build.

The build writes `_build_info.py` next to this file before freezing it
(see build.py). Running from the source tree, that file does not exist and
the values below say so.
"""

try:
    from baja_installer._build_info import BUNDLED_MODULES, DEFAULT_ENROLL_URL, VERSION
except ImportError:  # running from source, not from a build
    VERSION = "0.0.0+source"
    DEFAULT_ENROLL_URL = None
    BUNDLED_MODULES = None


def bundled_modules() -> list[str]:
    """The module list of this build (or, from source, of the source tree)."""
    if BUNDLED_MODULES is not None:
        return list(BUNDLED_MODULES)
    from baja_installer.bundled_modules import discover_bundled_modules

    return [name for name in discover_bundled_modules() if name != "baja_installer._build_info"]
