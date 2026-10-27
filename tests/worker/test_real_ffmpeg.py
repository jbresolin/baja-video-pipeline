"""Optional: make_proxy and process with the real ffmpeg on a tiny generated clip.

Skipped automatically when ffmpeg/ffprobe are not installed (for example in
CI). Everything else is the usual in-memory harness, so this checks that
the worker's real commands, overlay PNG and file handling produce videos
of the right shape. Run only these with:  uv run pytest -m integration
"""

import shutil
import subprocess

import pytest

from shared.ffmpeg import X264_ENCODER, build_test_clip_command
from shared.media_probe import MediaInfo
from shared.models import MakeProxyTask, ProcessTask
from tests.worker.fake_orchestrator import BASE_URL
from worker.media_tools import MediaTools

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed"),
]

REAL_MEDIA = MediaTools("ffmpeg", "ffprobe", X264_ENCODER)


@pytest.fixture
def held_clip_job(harness, tmp_path):
    """A 3-second 640x360 test clip with sound, held by the worker."""
    clip = tmp_path / "source.mp4"
    subprocess.run(
        build_test_clip_command(clip, duration_s=3.0, width=640, height=360),
        check=True,
        capture_output=True,
    )
    raw_bytes = clip.read_bytes()
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)
    harness.env.media = REAL_MEDIA
    return job


def probe(path) -> MediaInfo:
    return REAL_MEDIA.probe(path)


def test_real_proxy_and_thumbnail(harness, held_clip_job):
    job = held_clip_job
    harness.claim_and_run(
        MakeProxyTask(
            job_id=job.job_id,
            raw_sha256=job.sha256,
            proxy_upload_url=f"{BASE_URL}/jobs/{job.job_id}/proxy",
            thumbnail_upload_url=f"{BASE_URL}/jobs/{job.job_id}/thumbnail",
        )
    )

    assert harness.orchestrator.failures == []
    # The uploaded proxy is a real 480-line video of the same length.
    proxy = probe(harness.orchestrator.stored_upload("proxies", job.job_id))
    assert proxy.height == 480
    assert proxy.duration_s == pytest.approx(3.0, abs=0.2)
    # The thumbnail is a JPEG (files start with the bytes FF D8).
    assert harness.orchestrator.thumbnails[job.job_id][:2] == b"\xff\xd8"


def test_real_final_render_is_trimmed_1080p_with_audio(harness, held_clip_job):
    job = held_clip_job
    harness.claim_and_run(
        ProcessTask(
            job_id=job.job_id,
            raw_sha256=job.sha256,
            has_audio=True,
            low_res=False,
            cut_in_s=0.5,
            cut_out_s=2.0,
            prova="Enduro",
            car_number=42,
            team_name="Equipe",
            passagem=3,
            show_pass=True,
            clip_relative_path="enduro/42/3.mp4",
            outbox_upload_url=f"{BASE_URL}/jobs/{job.job_id}/clip",
        )
    )

    assert harness.orchestrator.failures == []
    clip = probe(harness.orchestrator.stored_upload("outbox", job.job_id))
    # Scaled to 1080 lines, trimmed to out - in = 1.5 s, sound kept.
    assert clip.height == 1080
    assert clip.duration_s == pytest.approx(1.5, abs=0.2)
    assert clip.has_audio
