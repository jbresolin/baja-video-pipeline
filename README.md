# Baja video pipeline v2

Takes pass videos that volunteers send to the Baja Bot on WhatsApp, has a
fleet of volunteer PCs download, proxy and edit them, lets reviewers set
the cut in a web UI, and delivers finished clips to the streaming PC for
OBS. Design: [`docs/design.md`](docs/design.md). API contract:
[`docs/api.md`](docs/api.md) and [`orchestrator/openapi.yaml`](orchestrator/openapi.yaml).

## Folder map

| Path | What |
| --- | --- |
| `shared/` | Package used by every service: pydantic models, job state machine, hashing, resumable downloads, chunked uploads, ffmpeg command builders, studio folder layout |
| `orchestrator/` | VPS service: intake, jobs DB, scheduling, review API, event log, manifest. `openapi.yaml` is its contract; `fakes/` has dev tools |
| `review_ui/` | VPS service: the reviewers' web UI (`/review`) and fleet view, its own package and image, talking to the orchestrator over its HTTP API (design §16) |
| `worker/` | Worker app, one codebase for the `master`, `worker` and `studio` roles |
| `installer/` | Windows installer for the worker: PyInstaller build, Inno Setup script, pinned ffmpeg/NSSM/Tailscale (`vendor.lock`), first-run Tailscale join. Volunteer guide (pt-BR) in `installer/README.md` |
| `tools/` | Repo tooling. `openapi_contract/` declares the API as FastAPI stubs; `generate_openapi.py` writes the YAML |
| `tests/<package>/` | Tests per package (kept outside the package folders, see `pyproject.toml`) |
| `tests/e2e/` | Whole-system test: real orchestrator, review UI and worker code (master, worker, studio) with a stub ffmpeg |
| `infra/` | Compose, Caddy, Tailscale ACLs, runbooks |
| `.github/workflows/` | `ci.yml` (lint, contract check, tests); `images.yml` (push `ghcr.io/jbresolin/baja-orchestrator` and `baja-review-ui`); `installer.yml` (Windows Setup .exe on `v*` tags) |
| `docs/` | Design and API docs |

## Getting started

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12. ffmpeg is optional
(only for the integration test and the dev tools).

```sh
uv sync                                  # create .venv with every package
uv run pytest                            # all tests
uv run pytest tests/shared               # one package
uv run pytest -m integration             # only tests needing real ffmpeg
uv run ruff check .                      # lint
uv run ruff format .                     # format
uv run python tools/generate_openapi.py  # after changing the contract or models
```

Dev tools:

```sh
uv run python orchestrator/fakes/make_test_clip.py .scratch/clip.mp4
uv run python orchestrator/fakes/fake_evolution_webhook.py .scratch/clip.mp4
```

## Rules

Code standards are in design §14: descriptive names, comments on intent,
files under ~300 lines, and test comments written for a complete
beginner. One branch per work item, PR to `main`.
