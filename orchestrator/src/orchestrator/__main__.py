"""Start the orchestrator: `python -m orchestrator` (reads BVP_* from the environment)."""

import logging
import sys

import uvicorn

from orchestrator.app import build_context, create_app
from orchestrator.config import ConfigError, load_settings
from shared.reviewers_file import ReviewersFileError


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = load_settings()
        context = build_context(settings)
    except (ConfigError, ReviewersFileError) as error:
        print(f"orchestrator: cannot start: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError as error:
        print(f"orchestrator: cannot start: file not found: {error.filename}", file=sys.stderr)
        return 2
    # The compose network is the boundary (design §8): listen on all interfaces.
    uvicorn.run(create_app(context), host="0.0.0.0", port=settings.port, proxy_headers=False)  # noqa: S104
    return 0


if __name__ == "__main__":
    sys.exit(main())
