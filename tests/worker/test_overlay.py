"""The overlay PNG (contract §30): legible text in a box at the bottom-left."""

from PIL import Image

from worker.overlay import OverlayText, final_frame_width, render_overlay_png

TEXT = OverlayText(
    prova="Enduro", car_number=42, team_name="Equipe Exemplo", passagem=3, show_pass=True
)


def test_lines_include_car_team_prova_and_pass():
    assert TEXT.lines() == ("#42  Equipe Exemplo", "Enduro · Passagem 3")


def test_pass_number_is_left_out_when_show_pass_is_false():
    hidden = OverlayText("Enduro", 42, None, 3, show_pass=False)
    assert hidden.lines() == ("#42", "Enduro")


def test_png_is_transparent_with_an_opaque_box_in_the_bottom_left(tmp_path):
    path = render_overlay_png(tmp_path / "overlay.png", TEXT, frame_width=1920)
    image = Image.open(path)
    assert image.mode == "RGBA"
    width, height = image.size
    # Fits in a 1080p frame, comfortably smaller than it.
    assert width < 1920 and height < 1080
    # Top-left corner of the canvas: fully transparent (the margin above
    # the box is nothing; ffmpeg places the canvas at the frame's bottom).
    assert image.getpixel((0, height - 1))[3] == 0  # left margin column
    # Inside the box: the dark, mostly opaque background.
    assert image.getpixel((60, 5))[3] > 100


def test_portrait_frame_gets_a_smaller_overlay_that_still_fits(tmp_path):
    # A phone video filmed upright: 1080 lines tall, only 608 wide.
    width = final_frame_width(1080, 1920)
    assert width == 608
    long_name = OverlayText(
        "Enduro de Resistência", 1234, "Equipe Com Um Nome Bem Comprido", 12, True
    )
    path = render_overlay_png(tmp_path / "overlay.png", long_name, frame_width=width)
    # The text was shrunk until the whole overlay fits the frame width.
    assert Image.open(path).size[0] <= width


def test_frame_width_follows_the_aspect_ratio():
    assert final_frame_width(3840, 2160) == 1920
    assert final_frame_width(None, None) == 1920  # unknown: assume 16:9
