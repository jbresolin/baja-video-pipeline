"""The tray's view of the Windows service (worker.tray.service), without Windows.

`sc query BajaWorker` prints a few lines; we read the STATE number from
them. `sc start` / `sc stop` normally just work for any user (the installer
grants that right); if Windows answers "access denied" (error 5) the tray
asks again behind a UAC prompt. A fake `run` stands in for sc.exe here.
"""

from worker.tray.service import (
    ERROR_ACCESS_DENIED,
    ERROR_SERVICE_ALREADY_RUNNING,
    ERROR_SERVICE_DOES_NOT_EXIST,
    ScResult,
    ServiceController,
    parse_query_output,
)
from worker.tray.state import ServiceState


def sc_query_output(state_number: int, word: str) -> str:
    """What `sc query BajaWorker` prints on a real Windows 11."""
    return (
        "\nSERVICE_NAME: BajaWorker \n"
        "        TYPE               : 10  WIN32_OWN_PROCESS  \n"
        f"        STATE              : {state_number}  {word}  \n"
        "                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)\n"
        "        WIN32_EXIT_CODE    : 0  (0x0)\n"
        "        SERVICE_EXIT_CODE  : 0  (0x0)\n"
        "        CHECKPOINT         : 0x0\n"
        "        WAIT_HINT          : 0x0\n"
    )


def test_the_state_number_is_read_from_sc_query():
    assert parse_query_output(ScResult(0, sc_query_output(4, "RUNNING"))) == ServiceState.RUNNING
    assert parse_query_output(ScResult(0, sc_query_output(1, "STOPPED"))) == ServiceState.STOPPED
    assert parse_query_output(ScResult(0, sc_query_output(2, "START_PENDING"))) == (
        ServiceState.STARTING
    )
    assert parse_query_output(ScResult(0, sc_query_output(3, "STOP_PENDING"))) == (
        ServiceState.STOPPING
    )


def test_a_missing_service_or_odd_output_is_unknown():
    # Error 1060: the service is not installed.
    missing = ScResult(ERROR_SERVICE_DOES_NOT_EXIST, "[SC] OpenService FAILED 1060")
    assert parse_query_output(missing) == ServiceState.UNKNOWN
    assert parse_query_output(ScResult(0, "nonsense")) == ServiceState.UNKNOWN


class FakeSc:
    """Records sc.exe calls and answers with prepared exit codes."""

    def __init__(self, exit_codes: dict[str, int]) -> None:
        self.exit_codes = exit_codes
        self.calls: list[list[str]] = []
        self.elevated_calls: list[list[str]] = []

    def run(self, arguments: list[str]) -> ScResult:
        self.calls.append(arguments)
        return ScResult(self.exit_codes.get(arguments[0], 0), "")

    def run_elevated(self, arguments: list[str]) -> bool:
        self.elevated_calls.append(arguments)
        return True


def test_stop_and_start_run_sc_for_the_bajaworker_service_only():
    sc = FakeSc({})
    controller = ServiceController(run=sc.run, run_elevated=sc.run_elevated)
    assert controller.stop() and controller.start()
    assert sc.calls == [["stop", "BajaWorker"], ["start", "BajaWorker"]]
    # It worked without elevation: no UAC prompt was needed.
    assert sc.elevated_calls == []


def test_access_denied_falls_back_to_a_uac_prompt():
    # An older install without the service-rights step: Windows says 5.
    sc = FakeSc({"stop": ERROR_ACCESS_DENIED})
    controller = ServiceController(run=sc.run, run_elevated=sc.run_elevated)
    assert controller.stop()
    assert sc.elevated_calls == [["stop", "BajaWorker"]]


def test_starting_a_service_that_already_runs_counts_as_success():
    sc = FakeSc({"start": ERROR_SERVICE_ALREADY_RUNNING})
    assert ServiceController(run=sc.run).start()
