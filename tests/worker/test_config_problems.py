"""A broken worker.json is shown, not crashed on (LIVESTACK-41 item 3).

Before: a missing comma in worker.json made the service crash with a
Python traceback, NSSM restarted it every few seconds and the status page
never came up. Now worker.config_problems.read_config() never raises: it
answers with the exact line and column and a plain pt-BR hint, the status
page shows that, and the tray turns red.

Each test writes a worker.json the way a person editing it by hand might
break it, and checks what the volunteer would be told.
"""

import threading

import httpx
import pytest

from shared.models import WorkerRole
from shared.testing.asgi_transport import SyncAsgiTransport
from shared.testing.credentials import PLACEHOLDER_WORKER_TOKEN
from worker.config import WorkerConfig, save_config
from worker.config_problems import ConfigProblemError, read_config, require_usable_config
from worker.pause import PauseSwitch
from worker.setup_page import build_setup_app
from worker.status import StatusBoard
from worker.tray.state import Colour, ServiceState, Toast, view_for

PORT = 8700

# A real-looking enrolled worker.json, as the installer and enrollment leave it.
GOOD_FILE_LINES = [
    "{",
    '  "orchestrator_url": "http://100.64.0.1:8080",',
    '  "enroll_url": "https://baja.example",',
    '  "worker_id": "w-1",',
    f'  "bearer_token": "{PLACEHOLDER_WORKER_TOKEN}",',
    '  "role": "worker",',
    '  "ffmpeg_path": "C:/Program Files/BajaWorker/ffmpeg/ffmpeg.exe",',
    '  "storage_cap_bytes": 107374182400',
    "}",
]


def write_lines(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "worker.json"


# --- read_config: the explanations -------------------------------------------


def test_a_good_file_reads_without_a_problem(config_path):
    write_lines(config_path, GOOD_FILE_LINES)
    reading = read_config(config_path)
    assert reading.usable
    assert reading.config.worker_id == "w-1"


def test_no_file_at_all_is_a_first_run_not_a_problem(config_path):
    # Before the installer ran there is no worker.json: all defaults.
    reading = read_config(config_path)
    assert reading.usable and not reading.config.is_enrolled


def test_a_missing_comma_names_the_line_the_column_and_says_comma(config_path):
    # Line 4 lost its comma at the end, so the JSON parser stumbles at the
    # start of line 5 (that is where it expected the comma at the latest).
    lines = list(GOOD_FILE_LINES)
    lines[3] = lines[3].rstrip(",")
    write_lines(config_path, lines)

    problem = read_config(config_path).problem

    assert problem is not None
    assert problem.line == 5 and problem.column == 3
    assert problem.line_text == lines[4]
    assert "linha 5, coluna 3" in problem.message
    assert "vírgula" in problem.hint
    assert problem.file == str(config_path)


def test_a_comma_too_many_before_the_closing_brace_is_explained(config_path):
    lines = list(GOOD_FILE_LINES)
    lines[-2] += ","  # "storage_cap_bytes": …, followed by }
    write_lines(config_path, lines)

    problem = read_config(config_path).problem

    assert problem.line == len(lines)
    assert "vírgula sobrando" in problem.hint


def test_a_single_backslash_in_a_windows_path_gets_the_backslash_hint(config_path):
    # The most likely hand edit on Windows: C:\BajaWorker instead of C:\\BajaWorker.
    lines = list(GOOD_FILE_LINES)
    lines.insert(1, '  "working_folder": "D:\\BajaWorker",')
    write_lines(config_path, lines)

    problem = read_config(config_path).problem

    assert problem.line == 2
    assert "\\\\" in problem.hint and "C:/BajaWorker" in problem.hint


def test_a_file_saved_by_notepad_with_a_byte_order_mark_still_reads(config_path):
    # Windows Notepad may save "UTF-8 with BOM": invisible bytes at the
    # start that a strict JSON parser rejects. That is not the volunteer's fault.
    config_path.write_bytes("\ufeff".encode() + "\n".join(GOOD_FILE_LINES).encode())
    assert read_config(config_path).usable


def test_an_unknown_key_is_named_with_its_line(config_path):
    lines = list(GOOD_FILE_LINES)
    lines.insert(2, '  "storage_cap_gb": 100,')  # a plausible typo for storage_cap_bytes
    write_lines(config_path, lines)

    problem = read_config(config_path).problem

    assert problem.message == 'O campo "storage_cap_gb" não existe.'
    assert problem.line == 3
    assert problem.line_text.strip() == '"storage_cap_gb": 100,'


def test_a_value_of_the_wrong_type_is_named(config_path):
    lines = list(GOOD_FILE_LINES)
    lines[-2] = '  "storage_cap_bytes": "cem gigas"'
    write_lines(config_path, lines)

    problem = read_config(config_path).problem

    assert '"storage_cap_bytes" tem um valor inválido' in problem.message
    assert problem.line == len(lines) - 1


def test_an_identity_with_a_deleted_field_is_a_problem_fixed_by_re_registering(config_path):
    # Someone deleted the bearer_token line. The file parses, but the worker
    # cannot work and should not silently fall back to the token form.
    lines = [line for line in GOOD_FILE_LINES if "bearer_token" not in line]
    write_lines(config_path, lines)

    reading = read_config(config_path)

    assert not reading.usable
    assert reading.config is not None  # parsed: "Registrar de novo" can start from it
    assert reading.problem.missing_fields == ("bearer_token",)
    assert reading.problem.fixable_by_re_registering
    assert '"bearer_token"' in reading.problem.message


def test_require_usable_config_raises_a_problem_error_instead_of_a_traceback(config_path):
    config_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigProblemError) as raised:
        require_usable_config(config_path)
    assert raised.value.problem.line == 1


# --- what the volunteer sees -------------------------------------------------


@pytest.fixture
def page(config_path):
    status = StatusBoard()
    app = build_setup_app(config_path, status, threading.Event(), PORT)
    return httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")


def test_the_status_page_comes_up_and_shows_the_file_line_and_hint(config_path, page):
    lines = list(GOOD_FILE_LINES)
    lines[3] = lines[3].rstrip(",")
    write_lines(config_path, lines)

    html = page.get("/").text

    # The page answered at all (before: the service crashed), and it says
    # what is wrong, where, and how to fix it.
    assert "linha 5, coluna 3" in html
    assert str(config_path) in html
    assert "vírgula" in html
    # The offending line is shown, with a caret under the column.
    assert "  ^" in html


def test_the_status_api_reports_the_problem_and_the_tray_turns_red(config_path, page):
    config_path.write_text('{"worker_id": "w-1",}', encoding="utf-8")

    document = page.get("/api/status").json()

    assert document["config_problem"]["line"] == 1
    assert document["enrolled"] is False
    view = view_for(document, ServiceState.RUNNING, was_running=True)
    assert view.colour == Colour.RED
    assert "worker.json" in view.headline
    assert Toast.CONFIG_PROBLEM in view.alerts


def test_an_identity_problem_offers_registrar_de_novo(config_path, page):
    write_lines(config_path, [line for line in GOOD_FILE_LINES if '"role"' not in line])
    html = page.get("/").text
    assert "Registrar de novo" in html
    assert page.get("/api/status").json()["can_re_register"] is True


def test_the_token_form_is_refused_while_the_file_is_broken(config_path, page):
    # Writing the form's answer would overwrite the file the volunteer is fixing.
    config_path.write_text("{ broken", encoding="utf-8")
    page.post("/setup", content="token=abc&folder=/data&cap_gb=10")
    assert config_path.read_text(encoding="utf-8") == "{ broken"


def test_pausing_never_overwrites_a_broken_file(config_path):
    # The pause switch is built at startup from worker.json; a broken file
    # must not crash it, and pressing "Pausar" must not replace the file.
    config_path.write_text("{ broken", encoding="utf-8")
    pause = PauseSwitch(config_path)
    assert pause.is_paused is False
    pause.pause()
    assert pause.is_paused is True
    assert config_path.read_text(encoding="utf-8") == "{ broken"


def test_a_good_enrolled_file_still_shows_the_normal_status_page(config_path, page):
    save_config(
        config_path,
        WorkerConfig(
            worker_id="w-1", bearer_token=PLACEHOLDER_WORKER_TOKEN, role=WorkerRole.WORKER
        ),
    )
    document = page.get("/api/status").json()
    assert document["config_problem"] is None and document["enrolled"] is True
