"""Waiting for the official result before rendering (LIVESTACK-62), in the job page's words.

Two pieces, both from the Job record alone:

* the box next to "Pronto" / "Salvar e reprocessar", "Esperar o resultado
  oficial (até N min)" (result_wait_box): on the page whenever one of
  those buttons is, and *shown* only while the result line or the clock
  is on; js/result_wait.js keeps that current as the reviewer ticks the
  toggles or picks another prova, and leaves the box out of the request
  while it is hidden;
* the status strip's banner while the render waits, with "Renderizar
  agora" (result_hold_note).

The orchestrator decides whether waiting makes sense (domain/result_hold.py):
the box only asks. The queue's marker is the `result_hold` macro.
"""

from dataclasses import dataclass

from review_ui.catalog import text
from review_ui.formatting import short_clock_text
from review_ui.job_view import ActionButton
from shared.models import Job, JobState, ReviewAction

# The buttons whose request may carry `wait_for_result`.
RENDERING_ACTIONS = {ReviewAction.READY.value, ReviewAction.REPROCESS.value}


@dataclass(frozen=True)
class ResultWaitBox:
    """ "Esperar o resultado oficial (até 15 min)", ticked by default."""

    label: str
    title: str
    # Shown at first: the job's prova has a result line or a clock, and one
    # of them is on. Hidden boxes are disabled, so they are not sent.
    shown: bool


def result_or_clock_is_on(job: Job) -> bool:
    """ "Mostrar resultado da passagem" or "Mostrar cronômetro" is in force for a
    prova that has it; the same rule js/result_wait.js applies to the form."""
    result_on = job.pass_result is not None and job.pass_result.configured and job.pass_result.show
    clock_on = job.pass_clock is not None and job.pass_clock.configured and job.pass_clock.show
    return result_on or clock_on


def result_wait_box(job: Job, actions: list[ActionButton]) -> ResultWaitBox | None:
    """The box, or None: no "Pronto"/"Salvar e reprocessar" on the page, or an
    orchestrator older than LIVESTACK-62 (it does not say how long it waits)."""
    if job.result_wait_minutes is None:
        return None
    if not any(button.value in RENDERING_ACTIONS for button in actions):
        return None
    minutes = job.result_wait_minutes
    return ResultWaitBox(
        label=text("result_wait.box", minutes=minutes),
        title=text("result_wait.box.title", minutes=minutes),
        shown=result_or_clock_is_on(job),
    )


@dataclass(frozen=True)
class ResultHoldNote:
    """The banner while a render waits: "Aguardando o resultado oficial (até 14:42)."."""

    text: str
    hint: str


def result_hold_note(job: Job) -> ResultHoldNote | None:
    """The banner for a held job, else None. Times in São Paulo, hours and minutes."""
    if job.state != JobState.READY or job.result_hold_until is None:
        return None
    until = short_clock_text(job.result_hold_until)
    return ResultHoldNote(
        text=text("result_hold.status", time=until),
        hint=text("result_hold.status.hint", time=until),
    )
