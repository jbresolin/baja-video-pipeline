"""Run the review UI against the fake orchestrator, to click through it by hand.

Not a test (pytest only collects test_*.py). From the repo root:

    uv run python tests/review_ui/dev_server.py [--media-dir .scratch/media]

then open http://127.0.0.1:8001/review/ and sign in as ana / secret.

--media-dir is optional: a folder with proxies/<job_id>.mp4 and
thumbs/<job_id>.jpg, served the way Caddy serves them on the VPS. For
example, make a proxy for the first sample job with:

    uv run python orchestrator/fakes/make_test_clip.py .scratch/media/proxies/job-awaiting-1.mp4
"""

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

# Running this file directly puts tests/review_ui/ first on sys.path; the
# repo root is needed instead, for `import tests.review_ui…`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from review_ui.app import create_app  # noqa: E402
from review_ui.settings import ReviewUiSettings  # noqa: E402
from tests.review_ui.fake_orchestrator import FakeOrchestrator  # noqa: E402
from tests.review_ui.sample_data import delivered_job, failed_job, make_job  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--media-dir", type=Path, help="folder with proxies/ and thumbs/")
    parser.add_argument("--port", type=int, default=8001)
    arguments = parser.parse_args()

    fake = FakeOrchestrator()
    fake.add_job(make_job("job-awaiting-1"))
    fake.add_job(make_job("job-lowres-1", low_res=True, waiting_reason=None))
    fake.add_job(failed_job("job-failed-1"))
    fake.add_job(delivered_job("job-done-1"))

    app = create_app(ReviewUiSettings(), orchestrator_http_client=fake.http_client())
    if arguments.media_dir:
        for folder in ("proxies", "thumbs"):
            path = arguments.media_dir / folder
            path.mkdir(parents=True, exist_ok=True)
            # Starlette's StaticFiles answers Range requests, like Caddy.
            app.mount(f"/{folder}", StaticFiles(directory=path), name=folder)

    uvicorn.run(app, host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()
