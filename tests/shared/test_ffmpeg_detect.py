"""Tests for shared.ffmpeg.detect_encoder (NVENC or libx264?).

detect_encoder runs ffmpeg twice: once to list encoders, once to try a
one-frame NVENC encode. Here we never run real ffmpeg: we pass a fake
"runner" function that returns whatever answer the test wants, and that
records which commands it was asked to run.
"""

import subprocess

import pytest

from shared.ffmpeg import (
    NVENC_ENCODER,
    X264_ENCODER,
    FfmpegNotFoundError,
    detect_encoder,
    encoder_is_listed,
)

# A shortened copy of real `ffmpeg -encoders` output.
ENCODERS_WITH_NVENC = """\
Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
"""
ENCODERS_WITHOUT_NVENC = ENCODERS_WITH_NVENC.replace(
    " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n", ""
)


class FakeRunner:
    """Pretends to be subprocess.run. Answers are given in order."""

    def __init__(self, *answers: tuple[int, str]) -> None:
        self.answers = list(answers)
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.commands.append(args)
        return_code, stdout = self.answers.pop(0)
        return subprocess.CompletedProcess(args, return_code, stdout=stdout, stderr="")


def test_nvenc_chosen_when_listed_and_the_trial_encode_works():
    # Answer 1: the encoder list includes NVENC. Answer 2: trial encode OK (exit 0).
    runner = FakeRunner((0, ENCODERS_WITH_NVENC), (0, ""))
    assert detect_encoder(run=runner) == NVENC_ENCODER
    # The second command really was an NVENC trial encode.
    assert "h264_nvenc" in runner.commands[1]


def test_libx264_chosen_when_nvenc_is_listed_but_no_gpu_can_run_it():
    # BtbN ffmpeg builds list NVENC even on PCs without an NVIDIA card; the
    # trial encode then fails (exit code 1), so we must fall back.
    runner = FakeRunner((0, ENCODERS_WITH_NVENC), (1, ""))
    assert detect_encoder(run=runner) == X264_ENCODER


def test_libx264_chosen_without_trying_when_nvenc_is_not_listed():
    runner = FakeRunner((0, ENCODERS_WITHOUT_NVENC))
    assert detect_encoder(run=runner) == X264_ENCODER
    # Only the listing ran: no point in a trial encode.
    assert len(runner.commands) == 1


def test_libx264_chosen_when_listing_fails():
    runner = FakeRunner((1, ""))
    assert detect_encoder(run=runner) == X264_ENCODER


def test_missing_ffmpeg_raises_a_clear_error():
    # subprocess.run raises FileNotFoundError when the program does not
    # exist; detect_encoder turns that into a named error the worker can
    # show on its setup page.
    def runner(args):
        raise FileNotFoundError(args[0])

    with pytest.raises(FfmpegNotFoundError):
        detect_encoder(ffmpeg_binary="not-a-real-ffmpeg", run=runner)


def test_encoder_is_listed_matches_whole_names_only():
    # "h264_nvenc" must not match a line that only mentions it in its
    # description, or a different encoder whose name merely contains it.
    output = " V....D hevc_nvenc   uses h264_nvenc-like hardware\n"
    assert encoder_is_listed(output, "h264_nvenc") is False
    assert encoder_is_listed(output, "hevc_nvenc") is True
