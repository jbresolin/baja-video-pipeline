"""The top speed under the clock (LIVESTACK-67): what a render draws, and the job page's view.

"Aceleração e Velocidade" (results API prova AVF) has two results tables
for one run: ACE, the acceleration time the clock freezes on, and VEL,
the top speed through the speed trap. The reviewer marks the moment the
car crosses the trap ("Velocidade" on the job page, next to "Largada");
from that frame to the end of the clip a second pill, under the clock,
shows the run's official speed ("37,2 km/h"). Run n of the speed table
pairs with run n of the clock's (docs/design.md §40).

The speed is part of the clock's corner: it is drawn only in a clip that
has a clock, under it (a decision of §40, so the corner has one layout).

A prova may instead show the speed for the last N seconds of the clip,
with no mark (LIVESTACK-68, the configuration's `speed.seconds_before_end`,
§42). The orchestrator works out where that starts and puts it where a
mark would go (RenderSpeed.speed_mark_s), so a worker draws it unchanged.
"""

from typing import Annotated

from pydantic import Field

from shared.models.common import ReadByClients, ReceivedByServer, Seconds, SecondsBeforeEnd
from shared.models.enums import PassResultProblem
from shared.models.pass_results import PassResultFetch

# The speed as written: "37,2". Short: a number with at most 3 decimals.
SpeedText = Annotated[str, Field(min_length=1, max_length=20)]
# The unit after it ("km/h"), as the configuration allows.
SpeedUnit = Annotated[str, Field(max_length=12)]
# 0 to 3 decimals, as the configuration allows.
SpeedDecimals = Annotated[int, Field(ge=0, le=3)]


class RenderSpeed(ReadByClients):
    """The speed a render draws (ProcessTask.pass_speed, RenderInputs.pass_speed).

    Read by clients: unknown fields are ignored.
    """

    # Where the speed pill appears, in raw-video seconds (like the largada):
    # the frame the reviewer marked as the car crossing the speed trap, or,
    # for a prova that shows it N s before the clip's end (LIVESTACK-68),
    # the start the orchestrator worked out from the cut-out. It stays to
    # the end of the clip.
    speed_mark_s: Seconds
    # The official speed as written, with a decimal comma and the
    # configured decimals, rounded half up: 37.18 km/h with 1 decimal = "37,2".
    text: SpeedText
    # Written after it, small and muted like the clock's unit; "" = none.
    unit: SpeedUnit = ""


class ReportedSpeed(ReceivedByServer):
    """The speed as a worker drew it, in RenderInputsReport.pass_speed.

    Received by the orchestrator: unknown fields are refused.
    """

    speed_mark_s: Seconds
    text: SpeedText
    unit: SpeedUnit = ""


class PassSpeed(ReadByClients):
    """Job.pass_speed: the job page's "Velocidade" mark and what it will draw.

    Read by clients: unknown fields are ignored.
    """

    # The job's prova has a `speed` block in the configuration: the job
    # page then offers the speed mark in the "Cronômetro" block.
    configured: bool = False
    # The reviewer's mark (Job.speed_mark_s), repeated here for the preview.
    # Kept but not used while `seconds_before_end` is set.
    speed_mark_s: Seconds | None = None
    # LIVESTACK-68: the prova shows the speed for the last N seconds of the
    # clip (the configuration's `speed.seconds_before_end`), with no mark;
    # None = from the reviewer's mark (§40). The job page then shows
    # "Velocidade: entra 3 s antes do fim do clipe." instead of the mark.
    seconds_before_end: SecondsBeforeEnd | None = None
    # Where the speed would start, in raw-video seconds: the mark, or with
    # `seconds_before_end` the clip's end (the cut-out, else the video's
    # duration) minus N, rounded down to the millisecond, never before the
    # cut-in. None = no mark, or no end known.
    speed_start_s: Seconds | None = None
    # The configuration's decimals and unit.
    decimals: SpeedDecimals = 0
    unit: SpeedUnit = ""
    # The official speed of the run from the latest speed table fetched, as
    # written ("37,2"); None while there is none, and `problem` says why
    # (the same reasons as the result line).
    official_text: str | None = None
    problem: PassResultProblem | None = None
    problem_detail: str | None = None
    # The fetch status of the speed table ("26BR_VEL"); None before the
    # table could be named (no event id).
    fetch: PassResultFetch | None = None
    # What a render would draw now: set when configured, the speed has a
    # start (`speed_start_s`), its value is known *and the clip has a clock*
    # (Job.pass_clock.render). None = no speed pill in the clip.
    render: RenderSpeed | None = None
