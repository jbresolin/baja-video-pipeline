"""`BajaWorker.exe --grant-service-rights` (installer/baja_installer/service_rights.py).

The tray icon runs without administrator rights, so the installer lets the
Users group start and stop *this one* service by adding one entry to its
security descriptor (an "SDDL" string). These tests check the string
surgery and the sc.exe calls with a fake sc; the Windows CI job checks the
real thing (a non-admin user stops and starts the service).
"""

import pytest

from baja_installer import cli
from baja_installer.service_rights import (
    EXIT_FAILED,
    EXIT_GRANTED,
    USERS_START_STOP_ACE,
    CommandResult,
    SddlError,
    add_users_start_stop,
    grant_service_rights,
)

# What `sc sdshow` prints for a service NSSM just created (Windows 11).
DEFAULT_SDDL = (
    "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"
    "(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)"
    "S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)"
)


def test_the_users_entry_goes_into_the_dacl_before_the_sacl():
    # "D:" … is the list of who may do what (DACL); "S:" … is auditing
    # (SACL). Our entry must be in the first part, or Windows ignores it.
    result = add_users_start_stop(DEFAULT_SDDL)
    dacl, sacl = result.split("S:")
    assert dacl.endswith(USERS_START_STOP_ACE)
    assert sacl == "(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)"
    # Everything that was there before is still there.
    assert result.replace(USERS_START_STOP_ACE, "") == DEFAULT_SDDL


def test_the_entry_allows_start_and_stop_but_not_change_or_delete():
    # RP = start, WP = stop. DC (change config), SD (delete), WD/WO (change
    # permissions/owner) and DT (pause) must not be granted to Users.
    rights = USERS_START_STOP_ACE.split(";")[2]
    pairs = {rights[index : index + 2] for index in range(0, len(rights), 2)}
    assert {"RP", "WP"} <= pairs
    assert not pairs & {"DC", "SD", "WD", "WO", "DT"}
    assert USERS_START_STOP_ACE.endswith(";;;BU)")  # BU = built-in Users


def test_a_descriptor_without_sacl_gets_the_entry_at_the_end():
    assert add_users_start_stop("D:(A;;CC;;;SY)") == "D:(A;;CC;;;SY)" + USERS_START_STOP_ACE


def test_granting_twice_changes_nothing():
    once = add_users_start_stop(DEFAULT_SDDL)
    assert add_users_start_stop(once) == once


def test_garbage_is_refused_rather_than_written_back():
    with pytest.raises(SddlError):
        add_users_start_stop("[SC] OpenService FAILED 1060")


class FakeSc:
    def __init__(self, shown: str, show_code: int = 0, set_code: int = 0) -> None:
        self.shown, self.show_code, self.set_code = shown, show_code, set_code
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> CommandResult:
        self.calls.append(arguments)
        if arguments[0] == "sdshow":
            return CommandResult(self.show_code, "\n" + self.shown + "\n\n")
        return CommandResult(self.set_code, "[SC] SetServiceObjectSecurity SUCCESS")


def test_grant_reads_the_descriptor_and_writes_it_back_with_the_entry():
    sc = FakeSc(DEFAULT_SDDL)
    assert grant_service_rights(run=sc, say=lambda _: None) == EXIT_GRANTED
    assert sc.calls[0] == ["sdshow", "BajaWorker"]
    assert sc.calls[1] == ["sdset", "BajaWorker", add_users_start_stop(DEFAULT_SDDL)]


def test_grant_is_a_no_op_when_the_entry_is_already_there():
    sc = FakeSc(add_users_start_stop(DEFAULT_SDDL))
    assert grant_service_rights(run=sc, say=lambda _: None) == EXIT_GRANTED
    assert [call[0] for call in sc.calls] == ["sdshow"]  # nothing written


def test_grant_fails_loudly_when_sc_fails():
    # Exit code 1 makes the installer list the step as failed (RunStep).
    assert grant_service_rights(run=FakeSc("", show_code=1060), say=lambda _: None) == EXIT_FAILED
    sc = FakeSc(DEFAULT_SDDL, set_code=5)
    assert grant_service_rights(run=sc, say=lambda _: None) == EXIT_FAILED


def test_the_exe_has_a_grant_service_rights_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "grant_service_rights", lambda: called.append(True) or 0)
    assert cli.main(["--grant-service-rights"]) == 0
    assert called == [True]
