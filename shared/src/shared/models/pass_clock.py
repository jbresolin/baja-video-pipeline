"""The running clock in the overlay (LIVESTACK-48): what a render draws, and the job page's view.

The reviewer marks the largada (the start of the run) on the job page; the
official time is the results API's raw run time for the passagem (the
prova's `clock.column` in the pass-results configuration). The clip then
shows a clock from the largada that freezes on the official time
(shared.pass_clock has the rule; docs/design.md §33).
"""

from typing import Annotated

from pydantic import Field

from shared.models.common import ReadByClients, ReceivedByServer, Seconds
from shared.models.enums import PassResultProblem
from shared.models.pass_results import PassResultFetch

# 0 to 3 decimals, as the configuration allows.
ClockDecimals = Annotated[int, Field(ge=0, le=3)]
# The official time in clock units (66 s with 1 decimal = 660). A day in
# thousandths is far below this; it only keeps a broken value out.
ClockUnits = Annotated[int, Field(ge=0, le=100_000_000)]


class RenderClock(ReadByClients):
    """The clock a render draws (ProcessTask.pass_clock, RenderInputs.pass_clock).

    Read by clients: unknown fields are ignored.
    """

    # Where the clock starts at 0, in raw-video seconds (like the cuts).
    largada_s: Seconds
    # Where it freezes, in units of the last decimal shown: 660 = "66,0".
    official_units: ClockUnits
    # Digits after the comma (the configuration's `clock.decimals`).
    decimals: ClockDecimals


class ReportedClock(ReceivedByServer):
    """The clock as a worker drew it, in RenderInputsReport.pass_clock.

    Received by the orchestrator: unknown fields are refused.
    """

    largada_s: Seconds
    official_units: ClockUnits
    decimals: ClockDecimals


class PassClock(ReadByClients):
    """Job.pass_clock: the job page's "Largada" and "Mostrar cronômetro".

    Read by clients: unknown fields are ignored.
    """

    # The job's prova has a `clock` block in the configuration: the job
    # page then offers the largada mark and the toggle. False = no clock.
    configured: bool = False
    # "Mostrar cronômetro" in force: Job.show_clock, else the prova's
    # `show_by_default`. Always False when not configured.
    show: bool = False
    # The reviewer's mark (Job.largada_s), repeated here for the preview.
    largada_s: Seconds | None = None
    # The configuration's decimals (0 when not configured).
    decimals: ClockDecimals = 0
    # The official time from the latest results fetched, in clock units and
    # as the clock writes it ("66,0"); None while there is none, and
    # `problem` says why (the same reasons as the result line).
    official_units: ClockUnits | None = None
    official_text: str | None = None
    problem: PassResultProblem | None = None
    problem_detail: str | None = None
    # The fetch status of the table the official time comes from (the same
    # table as Job.pass_result's); None before the table could be named.
    fetch: PassResultFetch | None = None
    # What a render would draw now: set when configured, shown, the largada
    # is marked and the official time is known; None = no clock in the clip.
    render: RenderClock | None = None
