"""The frozen exe's module list (installer/baja_installer/bundled_modules.py).

PyInstaller only bundles modules it can see being imported. The build
passes this list as hidden imports and `BajaWorker.exe --check` imports
every entry, so these tests make sure, on Linux, that the list is complete
and that every entry really imports.
"""

import importlib
from pathlib import Path

from baja_installer import build_info
from baja_installer.bundled_modules import (
    RUNTIME_IMPORTED_MODULES,
    discover_bundled_modules,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def module_names_on_disk(source_root: Path, package: str) -> set[str]:
    """Every .py file under source_root/package, as a dotted module name."""
    names = set()
    for path in (source_root / package).rglob("*.py"):
        parts = path.relative_to(source_root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return names


def test_every_module_in_the_frozen_list_imports():
    # This is the same list --check imports inside the exe. If one of them
    # fails here, the exe would fail on a volunteer's PC.
    failures = []
    for name in build_info.bundled_modules():
        try:
            importlib.import_module(name)
        except Exception as error:
            failures.append(f"{name}: {error!r}")
    assert failures == []


def test_every_worker_and_shared_module_is_in_the_list():
    # Walk the source folders independently of the code under test and
    # check nothing was left out (e.g. a new module added next month).
    on_disk = module_names_on_disk(REPO_ROOT / "worker" / "src", "worker")
    on_disk |= module_names_on_disk(REPO_ROOT / "shared" / "src", "shared")
    on_disk = {name for name in on_disk if not name.startswith("shared.testing")}

    assert on_disk - set(discover_bundled_modules()) == set()


def test_test_helpers_are_not_bundled():
    # shared.testing holds fakes for the test suite; the exe does not need it.
    assert not any(name.startswith("shared.testing") for name in discover_bundled_modules())


def test_uvicorns_run_time_imports_are_listed():
    # uvicorn imports these by name from strings, which PyInstaller cannot
    # see; without them the setup page (port 8700) would not start.
    modules = set(discover_bundled_modules())
    assert set(RUNTIME_IMPORTED_MODULES) <= modules
    assert {"uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.lifespan.on"} <= modules


def test_the_generated_build_info_module_is_always_bundled():
    # build.py writes baja_installer/_build_info.py just before freezing;
    # it must be a hidden import even though it does not exist in git.
    assert "baja_installer._build_info" in discover_bundled_modules()


def test_from_source_the_build_info_says_it_is_not_a_build():
    # Running from the repo (not frozen), there is no stamped file.
    assert build_info.VERSION == "0.0.0+source"
    assert "baja_installer._build_info" not in build_info.bundled_modules()
