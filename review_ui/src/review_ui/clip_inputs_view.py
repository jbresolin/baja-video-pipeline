"""A clip that no longer matches the review, in words (LIVESTACK-43).

The orchestrator sends `clip_inputs` on a job and on each clips-page
entry: what the newest clip was rendered from, what a render would use
now, and which inputs differ. This module turns that into the sentences
the job page's warning banner and the clips page's "desatualizado" detail
show, one per differing input, e.g.

    O clipe no ar usa corte 0:03–0:12; a revisão agora diz 0:05–0:12.

Plain functions over the API models, so they are tested without a server.
"""

from dataclasses import dataclass

from review_ui.catalog import text
from review_ui.formatting import format_duration, format_timecode
from shared.models import ClipInputsComparison, JobState, NameStyle, RenderInputField, RenderInputs

# While the job waits for (or is in) its new rendering, the version on air
# stays until the new one arrives: the banner says a new one is coming
# instead of asking for a reprocess.
NEW_VERSION_COMING_STATES = frozenset({JobState.READY, JobState.CLAIMED})


@dataclass(frozen=True)
class OutdatedClip:
    """What the warning says: which version, every difference, what to do."""

    version: int
    differences: list[str]
    # The closing sentence: reprocess to fix it, or a new version is on its way.
    next_step: str


def _cut_text(cut_in: float | None, cut_out: float | None, precise: bool) -> str:
    """ "0:03–0:12", or with milliseconds ("0:03,250–0:12,000") when a cut needs them."""
    show = format_timecode if precise else format_duration
    return f"{show(cut_in)}–{show(cut_out)}"


def _needs_milliseconds(*values: float | None) -> bool:
    return any(value is not None and abs(value - round(value)) > 0.0005 for value in values)


def _value(value: object) -> str:
    return text("outdated.no_value") if value is None or value == "" else str(value)


def _style_note(field: RenderInputField, clip: RenderInputs, now: RenderInputs) -> str:
    """ " (agora: nome completo nas Configurações)" when the Settings' choice changed."""
    if field == RenderInputField.TEAM_NAME:
        before, after = clip.team_name_style, now.team_name_style
    else:
        before, after = clip.school_name_style, now.school_name_style
    if after is None or before == after:
        return ""
    kind = "team" if field == RenderInputField.TEAM_NAME else "school"
    style = "full" if after == NameStyle.FULL else "short"
    return text("outdated.style_changed", style=text(f"outdated.style.{kind}.{style}"))


def difference_sentence(field: RenderInputField, clip: RenderInputs, now: RenderInputs) -> str:
    """One sentence for one differing input."""
    if field == RenderInputField.CUT:
        precise = _needs_milliseconds(clip.cut_in_s, clip.cut_out_s, now.cut_in_s, now.cut_out_s)
        return text(
            "outdated.cut",
            clip=_cut_text(clip.cut_in_s, clip.cut_out_s, precise),
            now=_cut_text(now.cut_in_s, now.cut_out_s, precise),
        )
    if field == RenderInputField.SHOW_PASS:
        return text(
            "outdated.show_pass.hidden_now" if clip.show_pass else "outdated.show_pass.shown_now"
        )
    if field == RenderInputField.RAW:
        return text("outdated.raw")
    if field in (RenderInputField.TEAM_NAME, RenderInputField.SCHOOL_NAME):
        attribute = (
            "overlay_team_name" if field == RenderInputField.TEAM_NAME else "overlay_school_name"
        )
        return text(
            f"outdated.{field.value}",
            clip=_value(getattr(clip, attribute)),
            now=_value(getattr(now, attribute)),
            why=_style_note(field, clip, now),
        )
    # prova, car_number, passagem: the same field on both sides.
    return text(
        f"outdated.{field.value}",
        clip=_value(getattr(clip, field.value)),
        now=_value(getattr(now, field.value)),
    )


def outdated_clip(
    comparison: ClipInputsComparison | None, state: JobState | None = None
) -> OutdatedClip | None:
    """The banner's content, or None when the clip matches (or nothing is known)."""
    if comparison is None or not comparison.changed:
        return None
    differences = [
        difference_sentence(field, comparison.clip, comparison.now) for field in comparison.changed
    ]
    if state in NEW_VERSION_COMING_STATES:
        next_step = text("outdated.new_version_coming", version=comparison.version + 1)
    else:
        next_step = text("outdated.reprocess_to_fix", version=comparison.version + 1)
    return OutdatedClip(comparison.version, differences, next_step)
