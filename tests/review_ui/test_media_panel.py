"""Tests for the "File details" panel under the player on the job page.

Setup: the fake orchestrator gets a job whose `media_info` is a trimmed
ffprobe report of an iPhone HDR video (what the first holder sends with
the proxy). The panel must show the readable facts, an HDR badge, the
phone, and "location present" without ever showing coordinates.
"""

from review_ui.media_info_view import media_panel
from shared.models import ProbeReport
from tests.review_ui.sample_data import make_job

IPHONE_REPORT = ProbeReport(
    format={
        "format_long_name": "QuickTime / MOV",
        "duration": "31.5",
        "size": "71234567",
        "bit_rate": "18072000",
        "tags": {
            "com.apple.quicktime.make": "Apple",
            "com.apple.quicktime.model": "iPhone 15 Pro",
            "com.apple.quicktime.software": "17.5",
            "major_brand": "qt  ",
        },
    },
    streams=[
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "profile": "Main 10",
            "width": 1920,
            "height": 1080,
            "display_aspect_ratio": "16:9",
            "color_transfer": "smpte2084",
            "avg_frame_rate": "30/1",
            "r_frame_rate": "30/1",
        },
        {"codec_type": "audio", "codec_name": "aac", "channel_layout": "stereo"},
    ],
    location_present=True,
)


def test_the_panel_shows_the_files_facts_under_the_player(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-iphone", media_info=IPHONE_REPORT))

    html = ui.get("/review/jobs/job-iphone").text

    # The panel is on the page, after the player's transport bar.
    assert 'aria-label="Detalhes do arquivo"' in html
    assert html.index('id="cut-bar"') < html.index('aria-label="Detalhes do arquivo"')
    # The facts a reviewer asked for, in readable form.
    assert "Apple iPhone 15 Pro (iOS 17.5)" in html
    assert "hevc (Main 10)" in html
    assert "1920×1080 (16:9)" in html
    assert "30 fps" in html
    # Decimal comma, the Brazilian way.
    assert "18,1 Mbps" in html
    # PQ transfer → the plain HDR badge.
    assert 'class="badge range-hdr">HDR<' in html
    # Location: only the fact, never coordinates (there are none to show).
    assert "tinha localização" in html
    # Unknown tags go in the collapsed list.
    assert "<summary>Todas as tags (1)</summary>" in html
    assert "major_brand" in html


def test_a_job_without_a_report_yet_says_it_will_appear(ui):
    # The sample job has no media_info (no holder probed it yet).
    html = ui.get("/review/jobs/job-awaiting-1").text
    assert 'aria-label="Detalhes do arquivo"' not in html
    assert "Os detalhes do arquivo aparecem depois que um worker com o original" in html


def test_rows_with_unknown_values_are_left_out():
    # A report with only a codec: the panel has that row and "Áudio: nenhum",
    # not a list of dashes.
    report = ProbeReport(streams=[{"codec_type": "video", "codec_name": "h264"}])
    panel = media_panel(make_job("job-bare", media_info=report))
    assert panel.rows == [("Vídeo", "h264"), ("Áudio", "nenhum")]
    assert panel.dynamic_range == "SDR"
