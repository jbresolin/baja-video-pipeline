"""The running clock on the job page, in words, and the overlay preview's data (LIVESTACK-48).

The orchestrator sends `Job.pass_clock`: whether the job's prova has a
clock, "Mostrar cronômetro" in force, the largada, the official time and
what a render would draw now. This module turns that into the job page's
"Cronômetro" block, and gathers what js/overlay_preview.js draws over the
player. Plain functions over the API models, tested without a server.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.pass_result_view import fetch_warning, problem_sentence
from shared.models import Job, PassClock


@dataclass(frozen=True)
class PassClockBox:
    """What the job page shows in "Cronômetro"."""

    # The checkbox's state: the toggle in force.
    show: bool
    # The largada field's value ("12.345"), "" when not marked.
    largada_value: str
    # "Na tarja: da largada até 66,0 s, e para.", or what is missing.
    status: str
    # The orchestrator's English details of a problem, or None.
    detail: str | None
    # "API de resultados inacessível desde …", only when the result block
    # (which says it otherwise) is not on the page.
    fetch_warning: str | None
    fetch_error: str | None


def clock_status(clock: PassClock) -> tuple[str, str | None]:
    """The status line and the English detail, from the clock as it is saved."""
    if clock.official_text is None:
        reason = problem_sentence(clock.problem)
        return f"{text('pass_clock.no_official')} {reason}", clock.problem_detail
    if clock.render is not None:
        return text("pass_clock.on_air", official=clock.official_text), None
    if not clock.show:
        return text("pass_clock.not_burned", official=clock.official_text), None
    return text("pass_clock.no_largada", official=clock.official_text), None


def pass_clock_box(
    clock: PassClock | None, now: datetime, *, result_box_shown: bool
) -> PassClockBox | None:
    """The block, or None when the prova has no clock (or the orchestrator is too old)."""
    if clock is None or not clock.configured:
        return None
    status, detail = clock_status(clock)
    warning = None if result_box_shown else fetch_warning(clock.fetch, now)
    return PassClockBox(
        show=clock.show,
        largada_value="" if clock.largada_s is None else f"{clock.largada_s:.3f}",
        status=status,
        detail=detail,
        fetch_warning=warning,
        fetch_error=clock.fetch.last_error if warning and clock.fetch is not None else None,
    )


def overlay_preview_data(job: Job) -> dict:
    """What js/overlay_preview.js draws, as JSON for the page.

    The texts come from the orchestrator as a render would get them now
    (the names after the Settings' choices, the result line, the official
    time in clock units); the page's own fields (passagem, toggles, largada,
    cuts) are read live from the form, so the preview follows unsaved edits.
    """
    result = job.pass_result
    clock = job.pass_clock
    return {
        "car_number": job.car_number,
        "team_name": job.clip_team_name,
        "school_name": job.clip_school_name,
        "prova": job.prova,
        "result_text": result.text if result is not None and result.configured else None,
        "clock": (
            {"official_units": clock.official_units, "decimals": clock.decimals}
            if clock is not None and clock.configured and clock.official_units is not None
            else None
        ),
    }
