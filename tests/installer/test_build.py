"""installer/build.py: version stamp, build info, PyInstaller and ISCC arguments.

The real PyInstaller run is exercised by CI (.github/workflows/installer.yml,
on Linux and Windows); here we test the pieces that decide what goes into
it, without running PyInstaller.
"""

import ast
from pathlib import Path

import pytest

from tests.installer.installer_scripts import load_installer_script

build = load_installer_script("build")


# --- version ----------------------------------------------------------------


def test_the_version_file_holds_major_minor_patch():
    # read_base_version raises if installer/VERSION is not like "1.2.3".
    assert build.VERSION_PATTERN.match(build.read_base_version())


def test_a_build_exactly_on_its_release_tag_reports_the_plain_version():
    # `git describe` prints just the tag when HEAD is that tag.
    assert build.stamp_version("0.1.0", "v0.1.0") == "0.1.0"


@pytest.mark.parametrize(
    ("describe", "expected"),
    [
        # 3 commits after the tag, at commit abc1234
        ("v0.1.0-3-gabc1234", "0.1.0+v0.1.0.3.gabc1234"),
        # same, with uncommitted changes
        ("v0.1.0-3-gabc1234-dirty", "0.1.0+v0.1.0.3.gabc1234.dirty"),
        # no tag in the history at all: describe prints the bare hash
        ("abc1234", "0.1.0+gabc1234"),
        ("abc1234-dirty", "0.1.0+gabc1234.dirty"),
        # a tag for another version (VERSION was bumped, not tagged yet)
        ("v0.0.9", "0.1.0+v0.0.9"),
        # not a git checkout
        (None, "0.1.0+unknown"),
    ],
)
def test_any_other_build_appends_the_git_description(describe, expected):
    # Two different builds must never report the same version, so anything
    # that is not exactly the release tag gets a "+<what git says>" suffix
    # (PEP 440 "local version": letters, digits and dots only).
    assert build.stamp_version("0.1.0", describe) == expected


def test_windows_gets_a_four_number_file_version():
    assert build.numeric_version("0.1.0") == "0.1.0.0"


# --- build info ---------------------------------------------------------------


def test_build_info_is_valid_python_with_the_stamped_values():
    source = build.build_info_source("0.1.0+gabc", "https://enroll.example", ["a", "b.c"])

    # Read the generated file back the way Python would and compare values.
    namespace: dict = {}
    exec(compile(source, "_build_info.py", "exec"), namespace)
    assert namespace["VERSION"] == "0.1.0+gabc"
    assert namespace["DEFAULT_ENROLL_URL"] == "https://enroll.example"
    assert namespace["BUNDLED_MODULES"] == ("a", "b.c")
    # And it is plain literals, nothing that runs code.
    ast.parse(source)


def test_build_info_without_an_enroll_url_stores_none():
    namespace: dict = {}
    exec(build.build_info_source("1.0.0", None, []), namespace)
    assert namespace["DEFAULT_ENROLL_URL"] is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://pipeline.example.org", "https://pipeline.example.org"),
        ("https://pipeline.example.org/", "https://pipeline.example.org"),
        # The endpoint spelling is accepted and reduced to the site, because
        # the worker appends "/enroll" itself (otherwise: /enroll/enroll, 404).
        ("https://pipeline.example.org/enroll", "https://pipeline.example.org"),
        ("https://pipeline.example.org/enroll/", "https://pipeline.example.org"),
        ("", None),
        (None, None),
    ],
)
def test_enroll_url_is_normalised(given, expected):
    assert build.validate_enroll_url(given) == expected


@pytest.mark.parametrize("bad", ["pipeline.example.org", "ftp://x", "https:// space"])
def test_enroll_url_that_is_not_http_is_refused(bad):
    with pytest.raises(build.BuildError):
        build.validate_enroll_url(bad)


# --- PyInstaller ----------------------------------------------------------------


def test_pyinstaller_builds_a_console_onedir_exe_from_entry_worker(tmp_path):
    arguments = build.pyinstaller_arguments(["worker.config"], tmp_path / "dist", tmp_path)

    # The first argument is the entry script; the rest are options.
    assert Path(arguments[0]).name == "entry_worker.py"
    assert "--onedir" in arguments
    assert "--console" in arguments
    assert arguments[arguments.index("--name") + 1] == "BajaWorker"


def test_every_bundled_module_becomes_a_hidden_import(tmp_path):
    modules = ["worker.config", "uvicorn.loops.auto", "shared.ffmpeg"]
    arguments = build.pyinstaller_arguments(modules, tmp_path / "dist", tmp_path)

    # Collect the value after each "--hidden-import".
    hidden = [arguments[i + 1] for i, a in enumerate(arguments) if a == "--hidden-import"]
    assert hidden == modules


def test_the_overlay_theme_files_are_bundled_as_data(tmp_path):
    # The clip overlay reads its font (TTF) and tokens.css from the package
    # worker.overlay_theme. PyInstaller only bundles Python modules unless
    # told, so without --collect-data the frozen worker would crash when
    # drawing its first overlay.
    arguments = build.pyinstaller_arguments([], tmp_path / "dist", tmp_path)

    collected = [arguments[i + 1] for i, a in enumerate(arguments) if a == "--collect-data"]
    assert "worker.overlay_theme" in collected


def test_the_overlay_theme_package_really_holds_the_files_to_bundle():
    # --collect-data copies the package's data files; make sure they are
    # there (and are not, say, ignored by git), so the bundle gets them.
    from importlib import resources

    theme = resources.files("worker.overlay_theme")
    for name in ("tokens.css", "BebasNeue-Regular.ttf", "BebasNeue-LICENSE.txt", "ORIGIN.md"):
        assert theme.joinpath(name).is_file(), name


def test_the_localhost_page_script_is_bundled_as_data(tmp_path):
    # The status/setup page inlines worker/page_assets/page.js, read with
    # importlib.resources. Without --collect-data the frozen worker could
    # not show its page at all (the one thing that must always work).
    from importlib import resources

    arguments = build.pyinstaller_arguments([], tmp_path / "dist", tmp_path)

    collected = [arguments[i + 1] for i, a in enumerate(arguments) if a == "--collect-data"]
    assert "worker.page_assets" in collected
    assert resources.files("worker.page_assets").joinpath("page.js").is_file()


def test_the_tray_is_a_windowed_onedir_exe_from_entry_tray(tmp_path):
    # BajaWorkerTray.exe starts at every logon: a console program would
    # flash a black window, so it is built --windowed.
    arguments = build.tray_pyinstaller_arguments(tmp_path / "dist", tmp_path, windows=True)
    assert Path(arguments[0]).name == "entry_tray.py"
    assert arguments[arguments.index("--name") + 1] == "BajaWorkerTray"
    assert "--windowed" in arguments and "--console" not in arguments
    # pystray loads its Windows backend by name, so it must be listed.
    hidden = [arguments[i + 1] for i, a in enumerate(arguments) if a == "--hidden-import"]
    assert hidden == ["pystray._win32"]
    # On Linux (CI's frozen smoke build) pystray is not installed at all.
    linux = build.tray_pyinstaller_arguments(tmp_path / "dist", tmp_path, windows=False)
    assert "--hidden-import" not in linux


def test_the_tray_build_is_merged_into_the_worker_folder(tmp_path):
    # Set-up: two onedir outputs as PyInstaller makes them. Both have
    # _internal/python312.dll (the same file); only the tray has pystray.
    worker = tmp_path / "BajaWorker"
    tray = tmp_path / "BajaWorkerTray"
    (worker / "_internal").mkdir(parents=True)
    (worker / "BajaWorker.exe").write_text("worker exe")
    (worker / "_internal" / "python312.dll").write_text("worker copy")
    (tray / "_internal" / "pystray").mkdir(parents=True)
    (tray / "BajaWorkerTray.exe").write_text("tray exe")
    (tray / "_internal" / "python312.dll").write_text("tray copy")
    (tray / "_internal" / "pystray" / "_win32.pyc").write_text("backend")

    copied = build.merge_onedir(tray, worker)

    # The tray's exe now sits next to BajaWorker.exe…
    assert (worker / "BajaWorkerTray.exe").read_text() == "tray exe"
    # …its extra files were added…
    assert (worker / "_internal" / "pystray" / "_win32.pyc").exists()
    # …and files both builds have are left as the worker's.
    assert (worker / "_internal" / "python312.dll").read_text() == "worker copy"
    assert len(copied) == 2


# --- payload copy and ISCC ------------------------------------------------------


def test_ffmpeg_bin_folder_and_licence_are_copied_next_to_the_exe(tmp_path):
    # Set up: a folder shaped like BtbN's unpacked zip.
    bin_folder = tmp_path / "btbn" / "bin"
    bin_folder.mkdir(parents=True)
    (bin_folder / "ffmpeg.exe").write_bytes(b"exe")
    (bin_folder / "avcodec-63.dll").write_bytes(b"dll")
    (tmp_path / "btbn" / "LICENSE.txt").write_text("GPL")
    app = tmp_path / "dist" / "BajaWorker"
    app.mkdir(parents=True)

    target = build.copy_ffmpeg_folder(bin_folder, app, tmp_path / "btbn" / "LICENSE.txt")

    # ffmpeg.exe and its DLLs must sit together (Windows finds DLLs next to
    # the exe), and the GPL text ships with them.
    assert target == app / "ffmpeg"
    assert sorted(p.name for p in target.iterdir()) == [
        "LICENSE.txt",
        "avcodec-63.dll",
        "ffmpeg.exe",
    ]


def test_iscc_gets_every_define_the_inno_script_requires(tmp_path):
    command = build.iscc_command(
        Path("ISCC.exe"),
        version="0.1.0+gabc1234",
        app_folder=tmp_path / "app",
        tailscale_msi=tmp_path / "ts.msi",
        output_folder=tmp_path / "out",
    )

    # BajaWorker.iss stops with #error unless these four are defined.
    defines = {part.split("=")[0] for part in command if part.startswith("/D")}
    assert defines == {"/DAppVersion", "/DAppNumericVersion", "/DDistDir", "/DTailscaleMsi"}
    # The numeric version drops the "+..." part: Windows wants four numbers.
    assert "/DAppNumericVersion=0.1.0.0" in command
    assert command[-1].endswith("BajaWorker.iss")
