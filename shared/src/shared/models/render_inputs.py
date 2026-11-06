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

from shared.models.common import ApiModel, CarNumber, PassNumber, Seconds, Sha256Hex
from shared.models.enums import NameStyle


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


class RenderInputs(ApiModel):
    """The inputs of one rendering. Every field may be None: unknown or not set.

    The same shape describes "what a clip was rendered from" and
    "what a render would use now" (ClipInputsComparison).
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
    # True when the rendering worker reported these itself; False for the
    # orchestrator's snapshot of the task it handed out (older workers).
    reported_by_worker: bool = False


class RenderInputsReport(ApiModel):
    """PUT /jobs/{id}/render-inputs body: what the worker rendered a clip from.

    Sent under the process lease, after rendering and before the upload or
    the push, so the orchestrator has it when the clip becomes a version.
    The values are the ProcessTask's as the worker used them; the name
    choices are not here because the task carries only the chosen names.
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


class ClipInputsComparison(ApiModel):
    """The newest clip version's inputs next to what a render would use now.

    `changed` lists every input that differs; empty means the clip matches
    the review. A clip whose inputs were never recorded (rendered before
    LIVESTACK-43) has no comparison at all (None on the Job).
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
