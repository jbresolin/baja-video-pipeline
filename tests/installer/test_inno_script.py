"""Static checks on installer/BajaWorker.iss.

Inno Setup only runs on Windows; the Windows CI job compiles the script
and installs/uninstalls it for real. These Linux tests catch the cheap
mistakes earlier: a wrong port, a missing uninstall step, a name that does
not match the Python side. They read the script as text.
"""

import re
from pathlib import Path

from baja_installer import layout

ISS_PATH = Path(__file__).resolve().parents[2] / "installer" / "BajaWorker.iss"
ISS_BYTES = ISS_PATH.read_bytes()
ISS = ISS_BYTES.decode("utf-8-sig")


def code_procedure(name: str) -> str:
    """The text of one Pascal procedure/function in the [Code] section."""
    match = re.search(
        rf"^(procedure|function) {name}\b.*?^end;", ISS, flags=re.MULTILINE | re.DOTALL
    )
    assert match, f"{name} not found in BajaWorker.iss"
    return match.group(0)


def test_the_script_is_utf8_with_a_bom():
    # Inno Setup reads a script without a BOM in the PC's ANSI code page,
    # which would garble the Portuguese text (ç, ã, é).
    assert ISS_BYTES.startswith(b"\xef\xbb\xbf")


def test_one_uac_prompt_and_the_program_files_folder():
    assert "PrivilegesRequired=admin" in ISS
    assert "DefaultDirName={commonpf64}\\BajaWorker" in ISS
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in ISS


def test_the_data_folder_is_c_bajaworker_and_survives_uninstall():
    assert '#define DataDir "{sd}\\BajaWorker"' in ISS
    assert re.search(r'Name: "\{#DataDir\}"; Flags: uninsneveruninstall', ISS)


def test_the_service_name_matches_what_first_run_restarts():
    # first_run restarts the service by this name (windows.restart_service).
    assert f'#define ServiceName "{layout.SERVICE_NAME}"' in ISS


def test_the_service_runs_the_exe_in_service_mode_with_auto_start_and_restart():
    register = code_procedure("RegisterService")
    assert "--service --config ' + ConfigPath" in register
    assert "Start SERVICE_AUTO_START" in register
    assert "AppExit Default Restart" in register
    # stdout/stderr go to the data folder's logs.
    assert "AppStdout ' + LogsDir" in register and "AppStderr ' + LogsDir" in register
    assert "AppDirectory ' + DataDir" in register


def test_tailscale_is_installed_silently_only_when_missing():
    assert "Check: TailscaleIsMissing" in ISS
    assert "tailscale.exe" in code_procedure("TailscaleIsMissing")
    install = code_procedure("InstallTailscale")
    # The MSI properties from infra/tailnet/worker_join.md §1.
    for setting in (
        "/quiet",
        "TS_UNATTENDEDMODE=always",
        "TS_NOLAUNCH=1",
        "TS_ALLOWINCOMINGCONNECTIONS=always",
        "TS_ONBOARDING_FLOW=hide",
    ):
        assert setting in install


def test_the_data_folder_is_limited_to_system_and_administrators_by_sid():
    restrict = code_procedure("RestrictDataFolder")
    # SIDs work on every Windows language; names like "Administrators" do not.
    assert "/inheritance:r" in restrict
    assert "*S-1-5-18:(OI)(CI)F" in restrict  # SYSTEM
    assert "*S-1-5-32-544:(OI)(CI)F" in restrict  # Administrators
    # Nobody else: no Users (S-1-5-32-545) or Everyone (S-1-1-0) grant.
    assert "S-1-5-32-545" not in restrict and "S-1-1-0" not in restrict


def test_firewall_opens_8710_and_8720_inbound_on_all_profiles():
    add = code_procedure("AddFirewallRules")
    for port in ("8710", "8720"):
        assert re.search(rf"dir=in action=allow protocol=TCP localport={port}\b.*profile=any", add)


def test_uninstall_stops_the_service_removes_rules_and_says_raws_are_kept():
    uninstall = code_procedure("CurUninstallStepChanged")
    assert "StopAndRemoveService" in uninstall
    assert "DeleteFirewallRules" in uninstall
    assert "--leave-tailnet" in uninstall
    assert "foi mantida" in uninstall  # "the folder was kept"
    # And both rules are removed by name.
    delete = code_procedure("DeleteFirewallRules")
    assert "{#FileServerRule}" in delete and "{#ReceiverRule}" in delete


def test_the_installer_writes_its_config_before_starting_the_service():
    steps = code_procedure("CurStepChanged")
    # Order matters: folder ACL, then worker.json, then the service.
    order = [
        steps.index(step) for step in ("RestrictDataFolder", "--init-config", "RegisterService")
    ]
    assert order == sorted(order)


def test_the_start_menu_opens_the_setup_page():
    assert f'#define SetupPageUrl "{layout.SETUP_PAGE_URL}"' in ISS
    assert 'Section: "InternetShortcut"; Key: "URL"; String: "{#SetupPageUrl}"' in ISS
    assert 'Name: "{group}\\Baja Worker"; Filename: "{app}\\Baja Worker.url"' in ISS


def test_first_run_starts_after_the_wizard_but_not_in_silent_installs():
    run_line = next(
        line
        for line in ISS.splitlines()
        if line.startswith('Filename: "{app}\\BajaWorker.exe"; Parameters: "--first-run')
    )
    assert "postinstall" in run_line and "skipifsilent" in run_line


def test_no_code_line_starts_with_a_hash_character_literal():
    # Inno's preprocessor reads every line that starts with "#" as one of
    # its own directives (#define, #error…). A Pascal character literal such
    # as #13#10 at the start of a line therefore stops the compile with
    # "Unknown preprocessor directive". Keep them at the end of a line.
    offending = [
        number
        for number, line in enumerate(ISS.splitlines(), start=1)
        if line.lstrip().startswith("#") and line.lstrip()[1:2].isdigit()
    ]
    assert offending == []
