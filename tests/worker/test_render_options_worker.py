"""The worker's side of LIVESTACK-50: render features, and rendering the options.

* The worker declares what its version can render (render features): at
  enrollment and in every heartbeat. An orchestrator older than contract
  2.6.0 refuses the unknown heartbeat field with 400, so the worker then
  beats without it (and tries again after 5 minutes), instead of losing
  every heartbeat and with them its leases.
* A process task's options reach the render: the picture is turned and
  fitted into 1920×1080 from the raw's shape as ffprobe sees it, the
  overlay is always drawn 1920 wide, "Sem áudio" leaves the sound out, and
  the render-inputs report says which options were used.
"""

from dataclasses import replace

from PIL import Image

from shared.media_probe import MediaInfo
from shared.models import Framing
from tests.worker.fakes import FULL_HD_WITH_AUDIO
from tests.worker.test_process import hold_raw, process_task
from worker.enrollment import detect_capabilities
from worker.heartbeat import RETRY_RENDER_FEATURES_AFTER_SECONDS
from worker.render_features import DECLARED_RENDER_FEATURES

# What ffprobe says about a phone filmed upright: 1920×1080 frames stored
# lying down, with a note to turn them 90° clockwise.
UPRIGHT_PHONE = replace(FULL_HD_WITH_AUDIO, rotation=90)


# --- Declaring the render features ---


def test_every_heartbeat_declares_the_render_features(harness):
    harness.heartbeater.beat_once()

    [sent] = harness.orchestrator.heartbeats
    # pass_result: installer 0.5.0, LIVESTACK-47 (tests/worker/test_pass_result_worker.py).
    assert sent.render_features == ["framing", "no_audio", "pass_result"]
    assert sent.render_features == DECLARED_RENDER_FEATURES


def test_enrollment_declares_them_too():
    # No ffmpeg at that path: the capability probe falls back to libx264,
    # and the features are declared whatever the machine.
    capabilities = detect_capabilities("/no/such/ffmpeg")
    assert capabilities.render_features == DECLARED_RENDER_FEATURES


def test_an_older_orchestrator_refusing_the_field_gets_beats_without_it(harness):
    # Setup: the VPS still runs an orchestrator from before contract 2.6.0,
    # whose strict model answers 400 to the unknown `render_features`.
    harness.orchestrator.knows_render_features = False

    # What it does: one heartbeat.
    answer = harness.heartbeater.beat_once()

    # The beat still got through (sent again without the field), so leases
    # are renewed and the worker keeps working with the old orchestrator.
    assert answer is not None
    [accepted] = harness.orchestrator.heartbeats
    assert accepted.render_features is None

    # The next beats leave it out at once (no refused round trip each time)...
    harness.steady_clock.advance(RETRY_RENDER_FEATURES_AFTER_SECONDS - 1)
    assert harness.heartbeater.beat_once() is not None
    assert harness.orchestrator.heartbeats[-1].render_features is None

    # ...until 5 minutes have passed: the VPS may have been updated since.
    harness.orchestrator.knows_render_features = True
    harness.steady_clock.advance(2)
    harness.heartbeater.beat_once()
    assert harness.orchestrator.heartbeats[-1].render_features == DECLARED_RENDER_FEATURES


def test_other_refusals_are_not_mistaken_for_an_older_orchestrator(harness):
    # A 503 (or any error not about render_features) is a failed beat, as
    # always: nothing is resent and the field stays in the next beat.
    harness.orchestrator.heartbeats_to_fail = 1
    assert harness.heartbeater.beat_once() is None
    harness.heartbeater.beat_once()
    assert harness.orchestrator.heartbeats[-1].render_features == DECLARED_RENDER_FEATURES


# --- Rendering the options ---


def overlay_width_during_render(harness) -> list[int]:
    """Collects the overlay PNG's width while the render runs (the PNG is deleted after)."""
    widths = []

    def look(what):
        if what != "final render":
            return
        command = harness.media.command_for("final render")
        overlay = next(arg for arg in command if arg.endswith(".overlay.png"))
        with Image.open(overlay) as image:
            widths.append(image.width)

    harness.media.during_render = look
    return widths


def test_an_upright_phone_video_is_fitted_with_blurred_sides_and_a_full_width_overlay(
    harness, raw_bytes
):
    # Setup: the raw is an upright phone video, the task has the defaults.
    harness.media.info = UPRIGHT_PHONE
    job = hold_raw(harness, raw_bytes)
    widths = overlay_width_during_render(harness)

    harness.claim_and_run(process_task(job))

    graph = " ".join(harness.media.command_for("final render"))
    # The shape that counts is 1080×1920 (after the rotation note): fitted
    # 608×1080 in the middle of a blurred copy.
    assert "split=2[fill][front]" in graph
    assert "[front]scale=608:1080" in graph
    # The overlay is drawn for the 1920-wide clip (it was 608 before, cut off).
    assert widths == [1920]


def test_girar_and_cortar_reach_the_filter_graph(harness, raw_bytes):
    harness.media.info = UPRIGHT_PHONE
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job, rotation=180, framing=Framing.CROP, crop_position=0.0))

    graph = " ".join(harness.media.command_for("final render"))
    # Turned upside down first, then the 1080×608 band from the top.
    assert "[0:v]hflip,vflip,crop=1080:608:0:0,scale=1920:1080" in graph


def test_sem_audio_renders_without_sound(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job, no_audio=True))

    command = harness.media.command_for("final render")
    assert "-an" in command
    assert "loudnorm" not in " ".join(command)


def test_a_landscape_video_renders_exactly_as_before(harness, raw_bytes):
    # The defaults on a 16:9 raw: the old scale=-2:1080 graph, sound kept.
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job))

    graph = harness.media.command_for("final render")
    graph_text = graph[graph.index("-filter_complex") + 1]
    assert graph_text.startswith("[0:v]scale=-2:1080[base];")
    assert "loudnorm" in graph_text


def test_the_report_says_which_options_were_used(harness, raw_bytes):
    harness.media.info = UPRIGHT_PHONE
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(
        process_task(job, rotation=90, framing=Framing.BLACK_BARS, crop_position=0.4, no_audio=True)
    )

    [(job_id, report)] = harness.orchestrator.render_inputs
    assert job_id == job.job_id
    # The task's options as used; the orchestrator works out which showed.
    assert (report.rotation, report.framing, report.crop_position, report.no_audio) == (
        90,
        Framing.BLACK_BARS,
        0.4,
        True,
    )


def test_changed_options_are_rendered_again_not_reused(harness, raw_bytes):
    # Setup: a clip rendered with the sound, its lease then lost before
    # delivery; the sidecar next to it remembers the options.
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(process_task(job))
    renders_before = len([what for what, _ in harness.media.commands if what == "final render"])

    # The same job offered again with "Sem áudio": the kept clip is not
    # what is wanted, so it renders again.
    harness.claim_and_run(process_task(job, no_audio=True))

    renders = [what for what, _ in harness.media.commands if what == "final render"]
    assert len(renders) == renders_before + 1


def test_media_info_without_a_size_still_renders_as_full_hd(harness, raw_bytes):
    # A probe that gave no size cannot happen (probe_input refuses a file
    # without video), but the plan falls back to 16:9 rather than failing.
    harness.media.info = MediaInfo(duration_s=12.0, has_audio=True, width=1920, height=1080)
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(process_task(job))
    assert harness.orchestrator.failures == []
