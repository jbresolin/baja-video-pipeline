"""The "Detalhes" section of the worker's status page (127.0.0.1:8700).

What this part of the page is for: a volunteer, or João helping one on the
phone, opens "Detalhes" to see which PC this is, where it keeps its files
and what last went wrong.

What these tests prove:
  * every row is in Portuguese; the board's internal English notes
    ("working folder", "last heartbeat", "accepting new raws"…) are not
    dumped into the table any more (seen on 2026-11-06);
  * "Último erro" says how long ago the error was, and once the server
    answers again it says "resolvido", so an old HTTP 502 from a VPS
    restart no longer looks like a current problem.

Not covered here: the rest of the page (test_setup_page.py) and how the
heartbeat decides an error happened (test_connection_step.py).
"""

from datetime import UTC, datetime, timedelta

from shared.models import WorkerRole
from worker.config import WorkerConfig
from worker.local_api import build_status_document
from worker.pause import PauseSwitch
from worker.status import WORKING_FOLDER_KEY, StatusBoard
from worker.status_page import describe_last_error, render_status_body

# 21:40 in São Paulo on the evening of the first studio test.
EVENING = datetime(2026, 11, 7, 0, 40, tzinfo=UTC)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def enrolled_desktop() -> WorkerConfig:
    """João's desktop, enrolled as master."""
    return WorkerConfig(
        worker_id="70f220153f8143f99d73a89f92dbeba1",
        bearer_token="placeholder-bearer-token",
        role=WorkerRole.MASTER,
        worker_name="BresolinDKT",
    )


def status_document(board: StatusBoard, tmp_path) -> dict:
    """What the local API hands the page, for the enrolled desktop."""
    pause = PauseSwitch(tmp_path / "worker.json")
    return build_status_document(enrolled_desktop(), board, pause)


def details_section(board: StatusBoard, now: datetime, tmp_path) -> str:
    """The page's HTML from the "Detalhes" summary to the end of its table."""
    document = status_document(board, tmp_path)
    body = render_status_body(document, now)
    start = body.index("<summary>Detalhes</summary>")
    return body[start : body.index("</table>", start)]


def test_the_details_rows_are_all_in_portuguese(tmp_path):
    # Arrange: a running worker whose board holds the usual internal notes,
    # exactly as the daemon and the heartbeat write them.
    board = StatusBoard(clock=FakeClock(EVENING))
    board.set(WORKING_FOLDER_KEY, r"E:\BajaWorker")
    board.set("orchestrator", "connected")
    board.set("accepting new raws", "yes")
    board.set_now("last heartbeat")

    # Act: render the page and cut out the "Detalhes" table.
    details = details_section(board, EVENING, tmp_path)

    # Assert: the working folder appears under its Portuguese name, and none
    # of the English note names do.
    assert "Pasta de trabalho" in details and r"E:\BajaWorker" in details, (
        "the working folder must still be shown, labelled in Portuguese"
    )
    for english_name in ("working folder", "last heartbeat", "accepting new raws", "orchestrator"):
        assert english_name not in details, f"'{english_name}' is an internal note, not a row"


def test_a_current_error_says_how_long_ago_it_happened(tmp_path):
    # Arrange: the server stopped answering 2 minutes ago and has not come back.
    clock = FakeClock(EVENING - timedelta(minutes=2))
    board = StatusBoard(clock=clock)
    board.record_unreachable("POST /workers/70f2…/heartbeat: HTTP 502")
    clock.now = EVENING

    # Act: render the details.
    details = details_section(board, EVENING, tmp_path)

    # Assert: the error is shown with its age and without "resolvido".
    assert "HTTP 502 (há 2 min)" in details, "a current error shows how long ago it happened"
    assert "resolvido" not in details, "the server has not answered since, so it is not resolved"


def test_an_error_followed_by_a_good_heartbeat_reads_resolved(tmp_path):
    # Arrange: a 502 while the VPS restarted, then, 30 s later, a heartbeat
    # that got an answer (the situation in João's screenshot of 2026-11-06).
    clock = FakeClock(EVENING - timedelta(minutes=5))
    board = StatusBoard(clock=clock)
    board.record_unreachable("POST /workers/70f2…/heartbeat: HTTP 502")
    clock.now = EVENING - timedelta(minutes=4, seconds=30)
    board.record_contact()

    # Act: render the details 5 minutes after the error.
    details = details_section(board, EVENING, tmp_path)

    # Assert: the error is still there for reference, but marked as over.
    assert "HTTP 502 (há 5 min; resolvido)" in details, (
        "an error the worker recovered from must say it is resolved"
    )


def test_a_new_error_after_a_resolved_one_is_current_again(tmp_path):
    # Arrange: an error, a recovery, and then a new error.
    clock = FakeClock(EVENING - timedelta(minutes=10))
    board = StatusBoard(clock=clock)
    board.record_unreachable("HTTP 502")
    board.record_contact()
    clock.now = EVENING - timedelta(seconds=20)
    board.record_unreachable("connection refused")

    # Act: describe the last error now.
    document = status_document(board, tmp_path)
    text = describe_last_error(document["details"], EVENING)

    # Assert: the new error replaced the old one and is not marked resolved.
    assert text == "connection refused (há 20 s)", "the newest error is the current one"


def test_no_error_shows_a_dash(tmp_path):
    # Arrange: a worker that never had a problem.
    board = StatusBoard(clock=FakeClock(EVENING))

    # Act: describe the last error.
    document = status_document(board, tmp_path)
    text = describe_last_error(document["details"], EVENING)

    # Assert: a plain dash, not an empty cell or "None".
    assert text == "—", "with no error the row shows a dash"


def test_a_start_failure_shows_as_the_last_error(tmp_path):
    # Arrange: the daemon could not start (e.g. the working folder's drive
    # is missing); the lifecycle records that as an error.
    board = StatusBoard(clock=FakeClock(EVENING))
    board.record_error("o worker não conseguiu iniciar; veja logs/worker.log")

    # Act: render the details.
    details = details_section(board, EVENING, tmp_path)

    # Assert: the volunteer sees it under "Último erro" (before, it went to an
    # English note while "Último erro" said "—").
    assert "o worker não conseguiu iniciar" in details, "a start failure must be visible"
