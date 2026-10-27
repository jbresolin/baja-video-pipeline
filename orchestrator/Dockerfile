# Orchestrator image (LIVESTACK-24). Build from the REPO ROOT, because the
# orchestrator depends on the shared/ package of the same uv workspace:
#
#     docker build -f orchestrator/Dockerfile -t baja-orchestrator .
#
# Runs as an unprivileged user (uid 10001), listens on 8000. Every setting
# comes from BVP_* / TAILSCALE_* environment variables (docs/api.md §10).

FROM python:3.12-slim

# uv, pinned to the version the lockfile was made with.
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Third-party dependencies only, from the lockfile. This layer is reused
#    as long as the pyproject files and uv.lock do not change.
COPY pyproject.toml uv.lock README.md ./
COPY shared/pyproject.toml shared/pyproject.toml
COPY orchestrator/pyproject.toml orchestrator/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
RUN uv sync --locked --no-dev --package orchestrator --no-install-workspace

# 2. Our own code: shared + orchestrator, installed (not editable) into the venv.
COPY shared/src shared/src
COPY orchestrator/src orchestrator/src
RUN uv sync --locked --no-dev --package orchestrator --no-editable

RUN groupadd --system --gid 10001 bvp \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /app bvp
USER bvp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "orchestrator"]
