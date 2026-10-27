# Review UI image (design §16). Build from the REPO ROOT, because the
# package depends on shared/ and the workspace lock file:
#
#   docker build -f review_ui/Dockerfile -t ghcr.io/jbresolin/baja-review-ui:<tag> .
#
# Listens on 8001 inside the compose network; Caddy routes /review* to it.

FROM python:3.12-slim

# uv, pinned, copied from its official image (no curl | sh).
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Dependencies only, so this layer is cached until the lock file changes.
#    uv needs every workspace member's pyproject.toml to read the lock file,
#    even the members this image does not install.
COPY pyproject.toml uv.lock .python-version ./
COPY shared/pyproject.toml shared/
COPY orchestrator/pyproject.toml orchestrator/
COPY worker/pyproject.toml worker/
COPY review_ui/pyproject.toml review_ui/
RUN uv sync --locked --no-dev --package review-ui --no-install-workspace

# 2. Our own code: shared and review_ui only.
COPY shared/src shared/src
COPY review_ui/src review_ui/src
RUN uv sync --locked --no-dev --package review-ui --no-editable

# Run as an unprivileged user; the app needs no files of its own.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin reviewui
USER reviewui

ENV PATH="/app/.venv/bin:$PATH" \
    BVP_REVIEW_UI_PORT=8001
EXPOSE 8001

# python:3.12-slim has no curl or wget, so the check uses Python itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=4)"]

CMD ["python", "-m", "review_ui"]
