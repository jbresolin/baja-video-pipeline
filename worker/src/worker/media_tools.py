"""Run ffprobe and ffmpeg for the tasks, stoppable mid-render.

The command lines come from shared.ffmpeg / shared.media_probe; this module
only runs them. ffmpeg runs as a child process that we poll, so when the
task is cancelled (lease lost, shutdown) the process is terminated within a
fraction of a second instead of finishing a render nobody wants.

Tests replace MediaTools with a fake that writes small files instead.
"""

import logging
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from shared.ffmpeg import build_final_render_command, build_proxy_command, build_thumbnail_command
from shared.media_probe import MediaInfo, build_probe_command, parse_probe_output
from worker.leases import CancelToken

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 0.2
# How many of ffmpeg's last stderr lines go into a failure report.
STDERR_TAIL_LINES = 15
# On Windows, do not flash a console window for every ffmpeg run.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class MediaToolError(Exception):
    """ffprobe/ffmpeg could not handle the input: a real failure of the job.

    `message` is written for the reviewer (it ends up in Job.failure).
    """

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


class MediaTools:
    def __init__(self, ffmpeg_path: str, ffprobe_path: str, video_encoder: str) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.video_encoder = video_encoder

    # --- Probing ---

    def probe(self, media_path: Path) -> MediaInfo:
        args = build_probe_command(media_path, ffprobe_binary=self.ffprobe_path)
        try:
            finished = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError as missing:
            raise RuntimeError(f"cannot run ffprobe at '{self.ffprobe_path}'") from missing
        if finished.returncode != 0:
            tail = _last_lines(finished.stderr)
            raise MediaToolError(f"ffprobe cannot read the file: {tail}", finished.returncode)
        info = parse_probe_output(finished.stdout)
        if info.width is None:
            raise MediaToolError("the file has no video stream")
        return info

    # --- Rendering (each builds a shared command and runs it) ---

    def make_thumbnail(
        self, raw: Path, thumbnail: Path, at_seconds: float, cancel: CancelToken
    ) -> None:
        args = build_thumbnail_command(
            raw, thumbnail, at_seconds=at_seconds, ffmpeg_binary=self.ffmpeg_path
        )
        self.run_ffmpeg(args, cancel, what="thumbnail")

    def make_proxy(self, raw: Path, proxy: Path, info: MediaInfo, cancel: CancelToken) -> None:
        args = build_proxy_command(
            raw,
            proxy,
            self.video_encoder,
            ffmpeg_binary=self.ffmpeg_path,
            tone_map_hdr=info.is_hdr,
        )
        self.run_ffmpeg(args, cancel, what="proxy")

    def render_final(
        self,
        raw: Path,
        clip: Path,
        *,
        cut_in_s: float,
        cut_out_s: float,
        overlay_png: Path | None,
        info: MediaInfo,
        cancel: CancelToken,
    ) -> None:
        args = build_final_render_command(
            raw,
            clip,
            cut_in_s,
            cut_out_s,
            self.video_encoder,
            overlay_image=overlay_png,
            has_audio=info.has_audio,
            ffmpeg_binary=self.ffmpeg_path,
            tone_map_hdr=info.is_hdr,
        )
        self.run_ffmpeg(args, cancel, what="final render")

    # --- Running ---

    def run_ffmpeg(self, args: list[str], cancel: CancelToken, *, what: str) -> None:
        """Run one ffmpeg command to completion, or stop it when cancelled.

        Raises MediaToolError (with ffmpeg's last stderr lines) on a non-zero
        exit, and TaskCancelledError when the task was cancelled meanwhile.
        """
        logger.info("running ffmpeg (%s): %s", what, subprocess.list2cmdline(args))
        exit_code, stderr_tail = run_cancellable_process(args, cancel)
        if exit_code != 0:
            raise MediaToolError(
                f"ffmpeg {what} failed with exit code {exit_code}:\n{stderr_tail}", exit_code
            )


def run_cancellable_process(args: list[str], cancel: CancelToken) -> tuple[int, str]:
    """Start `args`, poll until it exits; terminate it if `cancel` fires.

    Returns (exit code, last lines of stderr). stderr goes through a pipe
    read by a background thread into a bounded buffer, so a chatty process
    can never block on a full pipe.
    """
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as missing:
        raise RuntimeError(f"cannot run '{args[0]}'") from missing

    stderr_lines: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    reader = _start_stderr_reader(process, stderr_lines)
    try:
        while process.poll() is None:
            if cancel.reason() is not None:
                _stop_process(process)
                cancel.raise_if_cancelled()
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        if process.poll() is None:  # an unexpected error above: never leave it running
            _stop_process(process)
        reader.join(timeout=5)
    return process.returncode, "".join(stderr_lines).strip()


def _start_stderr_reader(process: subprocess.Popen, sink: deque[str]) -> threading.Thread:
    def read_all() -> None:
        for line in process.stderr:
            sink.append(line)

    reader = threading.Thread(target=read_all, name="ffmpeg-stderr", daemon=True)
    reader.start()
    return reader


def _stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _last_lines(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-STDERR_TAIL_LINES:])
