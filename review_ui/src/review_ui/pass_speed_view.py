"""The top speed under the clock on the job page, in words (LIVESTACK-67).

The orchestrator sends `Job.pass_speed` for "Aceleração e Velocidade":
whether the prova has a speed, the reviewer's speed mark, the official
speed ("37,2" km/h) or why there is none, and what a render would draw.
The speed is drawn only under the clock: marked and known but not drawn
means no clock would be, and the page says so. This module turns that into the speed row of the
"Cronômetro" block, and the overlay preview's speed data. Plain functions
over the API models, tested without a server.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.pass_result_view import fetch_warning, problem_sentence
from shared.models import PassSpeed
from shared.pass_clock import with_unit


@dataclass(frozen=True)
class PassSpeedBox:
    """What the "Cronômetro" block shows for the speed: its mark and status line."""

    # The mark field's value ("4.000"), "" when not marked.
    mark_value: str
    # "Velocidade na tarja: 37,2 km/h, …", or what is missing.
    status: str
    # The orchestrator's English details of a problem, or None.
    detail: str | None
    # "API de resultados inacessível desde …" for the speed's own table.
    fetch_warning: str | None
    fetch_error: str | None


def speed_text(speed: PassSpeed) -> str:
    """ "37,2 km/h" (with an ordinary space, as the page's sentences write units)."""
    return with_unit(speed.official_text or "", speed.unit)


def speed_status(speed: PassSpeed) -> tuple[str, str | None]:
    """The status line and the English detail, from the speed as saved."""
    if speed.official_text is None:
        reason = problem_sentence(speed.problem)
        return f"{text('pass_speed.no_official')} {reason}", speed.problem_detail
    shown = speed_text(speed)
    if speed.render is not None:
        return text("pass_speed.on_air", speed=shown), None
    if speed.speed_mark_s is None:
        return text("pass_speed.no_mark", speed=shown), None
    # Marked and known but not drawn: no clock would be (no largada, toggle
    # off, no official time), and the speed sits under the clock.
    return text("pass_speed.needs_clock", speed=shown), None


def pass_speed_box(speed: PassSpeed | None, now: datetime) -> PassSpeedBox | None:
    """The speed row, or None when the prova has no speed (or the orchestrator is too old)."""
    if speed is None or not speed.configured:
        return None
    status, detail = speed_status(speed)
    warning = fetch_warning(speed.fetch, now)
    return PassSpeedBox(
        mark_value="" if speed.speed_mark_s is None else f"{speed.speed_mark_s:.3f}",
        status=status,
        detail=detail,
        fetch_warning=warning,
        fetch_error=speed.fetch.last_error if warning and speed.fetch is not None else None,
    )


def overlay_preview_speed_data(speed: PassSpeed | None) -> dict | None:
    """The overlay preview's speed: the text and unit, or None when there is none to draw.

    The mark and the clock's toggles are read live from the form by
    js/overlay_preview.js, so the preview follows unsaved edits.
    """
    if speed is None or not speed.configured or speed.official_text is None:
        return None
    return {"text": speed.official_text, "unit": speed.unit}
