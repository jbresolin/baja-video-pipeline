"""Build BajaWorker.exe (PyInstaller onedir) and optionally the Setup .exe.

    uv run --group installer python installer/build.py \\
        [--enroll-url https://pipeline.example.org] [--compile-installer]

Steps:
 1. version = installer/VERSION, plus `git describe` when this commit is
    not exactly the tag v<VERSION> (e.g. "0.1.0+v0.1.0.3.gabc1234");
 2. write baja_installer/_build_info.py: version, default enroll URL and
    the module list (bundled_modules.py) that `--check` imports;
 3. freeze installer/entry_worker.py with every listed module as a hidden
    import → installer/dist/BajaWorker/;
 4. copy the bundled ffmpeg (bin folder + LICENSE) to dist/BajaWorker/ffmpeg/
    and nssm.exe to dist/BajaWorker/, from installer/vendor/ (fetch_vendor.py);
 5. with --compile-installer: run Inno Setup's ISCC on BajaWorker.iss →
    installer/output/BajaWorker-Setup-<version>.exe.

On Linux, steps 1–3 work the same (CI and the test suite use that);
`--ffmpeg-from-path` copies the system ffmpeg instead of the Windows build.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from baja_installer.bundled_modules import discover_bundled_modules

INSTALLER_FOLDER = Path(__file__).resolve().parent
REPO_ROOT = INSTALLER_FOLDER.parent
VERSION_FILE = INSTALLER_FOLDER / "VERSION"
ENTRY_SCRIPT = INSTALLER_FOLDER / "entry_worker.py"
INNO_SCRIPT = INSTALLER_FOLDER / "BajaWorker.iss"
BUILD_INFO_FILE = INSTALLER_FOLDER / "baja_installer" / "_build_info.py"
DIST_FOLDER = INSTALLER_FOLDER / "dist"
WORK_FOLDER = INSTALLER_FOLDER / "build"
OUTPUT_FOLDER = INSTALLER_FOLDER / "output"
APP_NAME = "BajaWorker"
DEFAULT_ISCC = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class BuildError(Exception):
    pass


# --- 1. version -----------------------------------------------------------


def read_base_version(version_file: Path = VERSION_FILE) -> str:
    version = version_file.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.match(version):
        raise BuildError(f"{version_file} must hold MAJOR.MINOR.PATCH, not {version!r}")
    return version


def git_describe(repo: Path = REPO_ROOT) -> str | None:
    """`git describe --tags --always --dirty --match v*`, or None without git."""
    try:
        finished = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty", "--match", "v*"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip() or None


def stamp_version(base_version: str, describe: str | None) -> str:
    """The version a build reports.

    Exactly on tag v<base>: just the base ("0.1.0"). Anywhere else the
    describe output is appended as a PEP 440 local part, so two different
    builds never claim the same version: "0.1.0+v0.1.0.3.gabc1234",
    "0.1.0+gabc1234.dirty", or "0.1.0+unknown" without git.
    """
    if describe == f"v{base_version}":
        return base_version
    if not describe:
        return f"{base_version}+unknown"
    local_part = re.sub(r"[^A-Za-z0-9]+", ".", describe).strip(".")
    if re.fullmatch(r"[0-9a-f]{7,40}(\.dirty)?", local_part):
        local_part = "g" + local_part  # a bare hash: mark it as one, like describe does
    return f"{base_version}+{local_part}"


def numeric_version(base_version: str) -> str:
    """Windows file versions have four numbers: "0.1.0" → "0.1.0.0"."""
    return f"{base_version}.0"


# --- 2. build info --------------------------------------------------------


def validate_enroll_url(url: str | None) -> str | None:
    if url is None or url == "":
        return None
    if not re.match(r"^https?://[^/\s]+", url):
        raise BuildError(f"--enroll-url must be an http(s) URL, not {url!r}")
    return url.rstrip("/")


def build_info_source(version: str, enroll_url: str | None, modules: list[str]) -> str:
    module_lines = "".join(f"    {name!r},\n" for name in modules)
    return (
        '"""Written by installer/build.py for one build. Do not edit or commit."""\n\n'
        f"VERSION = {version!r}\n"
        f"DEFAULT_ENROLL_URL = {enroll_url!r}\n"
        f"BUNDLED_MODULES = (\n{module_lines})\n"
    )


# --- 3. PyInstaller -------------------------------------------------------


def pyinstaller_arguments(modules: list[str], dist: Path, work: Path) -> list[str]:
    arguments = [
        str(ENTRY_SCRIPT),
        "--name",
        APP_NAME,
        "--onedir",
        # A console program: NSSM runs it hidden, and --first-run talks to
        # the volunteer in this window.
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work),
        "--paths",
        str(INSTALLER_FOLDER),
        # Not used by the worker; keeps the bundle smaller.
        "--exclude-module",
        "tkinter",
    ]
    for module in modules:
        arguments += ["--hidden-import", module]
    return arguments


def run_pyinstaller(arguments: list[str]) -> None:
    import PyInstaller.__main__  # only installed with the "installer" group

    PyInstaller.__main__.run(arguments)


# --- 4. third-party payloads ----------------------------------------------


def copy_ffmpeg_folder(ffmpeg_bin: Path, app_folder: Path, license_file: Path | None) -> Path:
    """Copy ffmpeg.exe, ffprobe.exe and their DLLs (BtbN's bin/ folder)."""
    target = app_folder / "ffmpeg"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ffmpeg_bin, target)
    if license_file is not None and license_file.is_file():
        shutil.copy2(license_file, target / "LICENSE.txt")  # GPL: ship the licence
    return target


def copy_system_ffmpeg(app_folder: Path) -> Path:
    """Linux smoke builds: copy the ffmpeg/ffprobe found on PATH."""
    target = app_folder / "ffmpeg"
    target.mkdir(parents=True, exist_ok=True)
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if not found:
            raise BuildError(f"--ffmpeg-from-path: {tool} is not on PATH")
        shutil.copy2(found, target / tool)
    return target


def copy_vendor_payloads(vendor_folder: Path, app_folder: Path) -> None:
    # Imported here: fetch_vendor is a script next to this one.
    sys.path.insert(0, str(INSTALLER_FOLDER))
    import fetch_vendor

    payloads = fetch_vendor.read_lock()
    ffmpeg_root = fetch_vendor.payload_path(vendor_folder, payloads["ffmpeg"])
    if not (ffmpeg_root / "bin").is_dir():
        raise BuildError(f"{ffmpeg_root / 'bin'} missing: run installer/fetch_vendor.py first")
    copy_ffmpeg_folder(ffmpeg_root / "bin", app_folder, ffmpeg_root / "LICENSE.txt")
    nssm = fetch_vendor.payload_path(vendor_folder, payloads["nssm"])
    if not nssm.is_file():
        raise BuildError(f"{nssm} missing: run installer/fetch_vendor.py first")
    shutil.copy2(nssm, app_folder / "nssm.exe")


def tailscale_msi_path(vendor_folder: Path) -> Path:
    sys.path.insert(0, str(INSTALLER_FOLDER))
    import fetch_vendor

    return fetch_vendor.payload_path(vendor_folder, fetch_vendor.read_lock()["tailscale"])


# --- 5. Inno Setup --------------------------------------------------------


def iscc_command(
    iscc: Path,
    *,
    version: str,
    app_folder: Path,
    tailscale_msi: Path,
    output_folder: Path,
    inno_script: Path = INNO_SCRIPT,
) -> list[str]:
    """ISCC with the /D defines BajaWorker.iss expects."""
    base = version.split("+")[0]
    return [
        str(iscc),
        f"/DAppVersion={version}",
        f"/DAppNumericVersion={numeric_version(base)}",
        f"/DDistDir={app_folder}",
        f"/DTailscaleMsi={tailscale_msi}",
        f"/O{output_folder}",
        str(inno_script),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--enroll-url", help="public site that routes POST /enroll")
    parser.add_argument("--vendor-dir", type=Path, default=INSTALLER_FOLDER / "vendor")
    parser.add_argument(
        "--ffmpeg-from-path", action="store_true", help="Linux: bundle the system ffmpeg"
    )
    parser.add_argument("--compile-installer", action="store_true", help="also run ISCC")
    parser.add_argument("--iscc", type=Path, default=DEFAULT_ISCC)
    args = parser.parse_args(argv)

    try:
        version = stamp_version(read_base_version(), git_describe())
        enroll_url = validate_enroll_url(args.enroll_url)
        modules = discover_bundled_modules()
        print(f"version {version}, enroll URL {enroll_url}, {len(modules)} modules")
        BUILD_INFO_FILE.write_text(build_info_source(version, enroll_url, modules), "utf-8")

        run_pyinstaller(pyinstaller_arguments(modules, DIST_FOLDER, WORK_FOLDER))
        app_folder = DIST_FOLDER / APP_NAME
        if args.ffmpeg_from_path:
            copy_system_ffmpeg(app_folder)
        else:
            copy_vendor_payloads(args.vendor_dir, app_folder)

        if args.compile_installer:
            command = iscc_command(
                args.iscc,
                version=version,
                app_folder=app_folder,
                tailscale_msi=tailscale_msi_path(args.vendor_dir),
                output_folder=OUTPUT_FOLDER,
            )
            print(" ".join(command))
            subprocess.run(command, check=True)
    except (BuildError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        # The stamped file belongs to this build only.
        BUILD_INFO_FILE.unlink(missing_ok=True)
    print(f"built {DIST_FOLDER / APP_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
