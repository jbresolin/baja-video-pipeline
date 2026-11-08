"""What a clip was rendered from, and how that compares with the review now (LIVESTACK-43).

A delivered clip does not change when a reviewer edits the job afterwards:
only a reprocess renders a new version. So every clip version remembers
its **render inputs** (cuts, review fields, the names its overlay shows,
the overlay theme, the raw), and the orchestrator compares the newest
version's inputs with what a render would use *now* (the review fields as
saved, the Settings' name choices as they are). Any difference means the
clip on air is out of date ("desatualizado" in the review UI).

Where the inputs come from (docs/api.md §5 "Render inputs"):

* the orchestrator snapshots them when a worker claims a process task:
  the task it hands out plus the Settings' name choices, which workers
  never see (they get the names already chosen);
* the rendering worker reports what it actually used
  (`PUT /jobs/{id}/render-inputs`, RenderInputsReport) before it uploads
  or pushes the clip; that report wins, with the overlay theme version
  that only the worker knows. A worker that reports nothing (older
  versions) leaves the snapshot in place, `reported_by_worker: false`.
"""

from enum import StrEnum

from pydantic import Field

from shared.models.common import (
    CarNumber,
    CropPosition,
    PassNumber,
    PassResultText,
    ReadByClients,
    ReceivedByServer,
    Rotation,
    Seconds,
    Sha256Hex,
)
from shared.models.enums import Framing, NameStyle
from shared.models.pass_clock import RenderClock, ReportedClock


class RenderInputField(StrEnum):
    """One input that changes a clip's picture, as listed in a comparison."""

    # The cut in and out points, compared together ("corte 0:03–0:12").
    CUT = "cut"
    PROVA = "prova"
    CAR_NUMBER = "car_number"
    PASSAGEM = "passagem"
    # Whether the overlay shows the pass number.
    SHOW_PASS = "show_pass"
    # The team name in the overlay's name pill (after the Settings' choice).
    TEAM_NAME = "team_name"
    # The school in the overlay's role pill (sigla or full name).
    SCHOOL_NAME = "school_name"
    # The raw video itself (never expected to differ; listed for completeness).
    RAW = "raw"
    # The reviewer's extra turn ("Girar", LIVESTACK-50).
    ROTATION = "rotation"
    # How a picture that is not 16:9 fills the frame, with the crop band's
    # place ("Enquadramento"). Never listed for a 16:9 picture.
    FRAMING = "framing"
    # Sound kept or dropped ("Sem áudio").
    AUDIO = "audio"
    # The official result line (LIVESTACK-47): published, corrected by the
    # judges, or turned on or off by the reviewer since the clip was made.
    PASS_RESULT = "pass_result"
    # The running clock (LIVESTACK-48): the largada moved, the official time
    # published or corrected, or the clock turned on or off.
    CLOCK = "clock"


class RenderInputs(ReadByClients):
    """The inputs of one rendering. Every field may be None: unknown or not set.

    The same shape describes "what a clip was rendered from" and
    "what a render would use now" (ClipInputsComparison).

    Read by clients: unknown fields are ignored.
    """

    raw_sha256: Sha256Hex | None = None
    cut_in_s: Seconds | None = None
    cut_out_s: Seconds | None = None
    prova: str | None = None
    car_number: CarNumber | None = None
    passagem: PassNumber | None = None
    show_pass: bool | None = None
    # The Settings' name choices in force (admin Settings, LIVESTACK-42).
    team_name_style: NameStyle | None = None
    school_name_style: NameStyle | None = None
    # The texts the overlay shows after those choices: name pill and the
    # school part of the role pill. None = left out (unknown team/school).
    overlay_team_name: str | None = None
    overlay_school_name: str | None = None
    # SHA-256 of the worker's vendored overlay theme (worker/overlay_theme/
    # tokens.css): which look the clip was drawn with. Only workers know
    # it, so it is None in the orchestrator's snapshot and in "now".
    overlay_theme_version: Sha256Hex | None = None
    # Render options (LIVESTACK-50). `framing` and `crop_position` as they
    # affect the picture (shared.framing.effective_framing): None when the
    # picture is 16:9 after the turn, `crop_position` only for "crop".
    # Inputs recorded before LIVESTACK-50 have rotation and no_audio None,
    # which the comparison reads as 0 and False (what was rendered then).
    rotation: Rotation | None = None
    framing: Framing | None = None
    crop_position: CropPosition | None = None
    no_audio: bool | None = None
    # The overlay's result line (ProcessTask.pass_result_text, LIVESTACK-47).
    # None = no result pill, which is also what inputs recorded before
    # LIVESTACK-47 mean. In "now": the text from the latest results the
    # orchestrator fetched, when the reviewer's toggle shows it.
    pass_result_text: PassResultText | None = None
    # The running clock (ProcessTask.pass_clock, LIVESTACK-48). None = no
    # clock, which is also what inputs recorded before LIVESTACK-48 mean.
    pass_clock: RenderClock | None = None
    # True when the rendering worker reported these itself; False for the
    # orchestrator's snapshot of the task it handed out (older workers).
    reported_by_worker: bool = False


class RenderInputsReport(ReceivedByServer):
    """PUT /jobs/{id}/render-inputs body: what the worker rendered a clip from.

    Sent under the process lease, after rendering and before the upload or
    the push, so the orchestrator has it when the clip becomes a version.
    The values are the ProcessTask's as the worker used them; the name
    choices are not here because the task carries only the chosen names.

    Received by the orchestrator: unknown fields are refused.
    """

    # The rendered file's SHA-256: which clip version these inputs belong to.
    clip_sha256: Sha256Hex
    raw_sha256: Sha256Hex
    cut_in_s: Seconds
    cut_out_s: Seconds
    prova: str
    car_number: CarNumber
    passagem: PassNumber
    show_pass: bool
    # ProcessTask.team_name and .school_short as drawn (None = left out).
    overlay_team_name: str | None = None
    overlay_school_name: str | None = None
    overlay_theme_version: Sha256Hex
    # The render options as the worker used them, i.e. the ProcessTask's
    # (LIVESTACK-50); the orchestrator works out which of them showed.
    # Left out by workers before 0.4.0, which rendered with no turn, no
    # framing and the sound kept.
    rotation: Rotation | None = None
    framing: Framing | None = None
    crop_position: CropPosition | None = None
    no_audio: bool | None = None
    # The result line as drawn (ProcessTask.pass_result_text; LIVESTACK-47).
    # Left out by workers before 0.5.0, which never get a task carrying one.
    pass_result_text: PassResultText | None = None
    # The clock as drawn (ProcessTask.pass_clock; LIVESTACK-48). Left out by
    # workers before 0.6.0, which never get a task carrying one.
    pass_clock: ReportedClock | None = None


class ClipInputsComparison(ReadByClients):
    """The newest clip version's inputs next to what a render would use now.

    `changed` lists every input that differs; empty means the clip matches
    the review. A clip whose inputs were never recorded (rendered before
    LIVESTACK-43) has no comparison at all (None on the Job).

    Read by clients: unknown fields are ignored.
    """

    # Which clip version was compared: the newest one (the one the studio
    # plays, or is about to get).
    version: int = Field(ge=1)
    # What that version was rendered from.
    clip: RenderInputs
    # What a render would use now: the review as saved, the Settings' name
    # choices as they are.
    now: RenderInputs
    changed: list[RenderInputField] = Field(default_factory=list)
