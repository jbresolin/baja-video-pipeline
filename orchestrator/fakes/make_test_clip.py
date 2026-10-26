"""Dev tool: generate a short synthetic MP4 (test pattern + tone) with ffmpeg.

uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4
uv run python orchestrator/fakes/make_test_clip.py .scratch/hd.mp4 --seconds 30 --size 1920x1080
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from shared.ffmpeg import build_test_clip_command


def parse_size(text: str) -> tuple[int, int]:
    """'1280x720' → (1280, 720)."""
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic test MP4.")
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--size", type=parse_size, default=(1280, 720), help="WIDTHxHEIGHT")
    parser.add_argument("--fps", type=int, default=30)
    arguments = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH; install it first", file=sys.stderr)
        return 2

    width, height = arguments.size
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    command = build_test_clip_command(
        arguments.output, arguments.seconds, width, height, arguments.fps
    )
    subprocess.run(command, check=True, capture_output=True)
    print(f"wrote {arguments.output} ({arguments.output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
