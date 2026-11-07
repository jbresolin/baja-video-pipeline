"""The job page's "Imagem e som" box: orientation, Girar, Enquadramento, Sem áudio (LIVESTACK-50).

The server renders the box as the job is saved; static/js/render_options.js
then keeps it in step with the form while the reviewer changes "Girar"
(the orientation line, whether "Enquadramento" applies, the player's
turn and the crop band). Both use the same rule as the worker
(shared.framing): a picture that is 16:9 after the turn is rendered as
before, so "Enquadramento" only matters for any other picture.
"""

from dataclasses import dataclass

from review_ui.catalog import text
from shared.framing import ROTATIONS, display_size, fills_frame_like_before
from shared.models import Framing, Job


@dataclass(frozen=True)
class RenderOptionsBox:
    # "Vídeo na vertical (1080×1920)." for the saved turn.
    orientation: str
    # False when the picture is 16:9 after the turn: "Enquadramento" is
    # hidden then (it changes nothing). True while the size is unknown.
    framing_applies: bool
    # (value, label) for the "Girar" select and the framing radio buttons.
    rotation_choices: list[tuple[int, str]]
    framing_choices: list[tuple[str, str]]


def orientation_text(width: int | None, height: int | None, rotation: int) -> str:
    """One sentence on the picture's shape after `rotation`; how players and the clip see it."""
    if not width or not height:
        return text("script.orientation.unknown")
    turned_width, turned_height = display_size(width, height, rotation)
    if turned_height > turned_width:
        key = "script.orientation.vertical"
    elif fills_frame_like_before(turned_width, turned_height):
        key = "script.orientation.horizontal"
    else:
        key = "script.orientation.other"
    return text(key, width=turned_width, height=turned_height)


def framing_applies(width: int | None, height: int | None, rotation: int) -> bool:
    if not width or not height:
        return True
    return not fills_frame_like_before(*display_size(width, height, rotation))


def render_options_box(job: Job) -> RenderOptionsBox:
    width, height = job.display_width, job.display_height
    return RenderOptionsBox(
        orientation=orientation_text(width, height, job.rotation),
        framing_applies=framing_applies(width, height, job.rotation),
        rotation_choices=[(degrees, text(f"render.rotation.{degrees}")) for degrees in ROTATIONS],
        framing_choices=[
            (framing.value, text(f"render.framing.{framing.value}")) for framing in Framing
        ],
    )
