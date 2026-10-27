"""Request/response bodies for the review UI endpoints (reviewer auth)."""

from pydantic import Field, model_validator

from shared.models.common import ApiModel, CarNumber, PassNumber, Seconds
from shared.models.enums import JobState, ReviewAction
from shared.models.job import JobSummary, check_cut_order


class ReviewQueueResponse(ApiModel):
    """GET /review/queue: oldest first, so nothing waits forever."""

    items: list[JobSummary]


class ReviewUpdate(ApiModel):
    """PATCH /jobs/{id}/review.

    Fields left out (None) keep their current value. With action "ready" or
    "reprocess", the job must end up with prova, car_number, passagem and
    both cuts set, otherwise the answer is 422 missing_review_fields.
    """

    # The Job.version the reviewer was looking at; 409 version_conflict if
    # somebody else saved in between.
    expected_version: int = Field(ge=1)
    action: ReviewAction = ReviewAction.SAVE
    prova: str | None = Field(default=None, min_length=1, max_length=64)
    car_number: CarNumber | None = None
    passagem: PassNumber | None = None
    show_pass: bool | None = None
    cut_in_s: Seconds | None = None
    cut_out_s: Seconds | None = None

    @model_validator(mode="after")
    def _cuts_in_order(self) -> "ReviewUpdate":
        check_cut_order(self.cut_in_s, self.cut_out_s)
        return self


class RegenerateProxyResponse(ApiModel):
    job_id: str
    # True when a make_proxy task is now waiting for a holder.
    queued: bool
    # Why it could not be queued right away, e.g. no holder is online.
    waiting_reason: str | None = None


class Team(ApiModel):
    """GET /teams/{car_number}: team resolved from the Baja app."""

    car_number: CarNumber
    team_name: str
    school: str | None = None


class ReviewQueueFilter(ApiModel):
    """Query parameters of GET /review/queue, as a model for validation."""

    states: list[JobState] = Field(default_factory=lambda: [JobState.AWAITING_REVIEW])
    limit: int = Field(default=100, ge=1, le=500)
