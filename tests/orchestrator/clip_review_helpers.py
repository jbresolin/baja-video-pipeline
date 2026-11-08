"""Shared steps for the "Ver clipe" and "Baixar clipe" tests (LIVESTACK-52).

They drive the orchestrator through its HTTP API, as workers and the
studio would: enroll PCs that declare the new abilities, render a clip
(through the outbox or pushed straight to the studio), send its preview.
"""

import hashlib

from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
    upload_in_chunks,
)
from shared.models import RenderFeature, TaskKind, WorkerRole
from shared.protocol import (
    CLIP_SHA256_HEADER,
    UPLOAD_LENGTH_HEADER,
    UPLOAD_OFFSET_HEADER,
    UPLOAD_SHA256_HEADER,
)

# What a 0.7.0 worker declares for these two abilities.
NEW_ABILITIES = [RenderFeature.CLIP_PREVIEW.value, RenderFeature.UPLOAD_CLIP.value]
# A stand-in preview: the bytes do not matter to the orchestrator.
SAMPLE_PREVIEW = b"small-preview-" * 300
OFFLINE_SECONDS = 60  # past the 45 s online window
MINUTE = 60
HOUR = 3600


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def beat(harness, worker, features=NEW_ABILITIES, **fields):
    """A heartbeat that declares `features` (each beat replaces the list;
    left out would mean "none")."""
    return harness.heartbeat(worker, render_features=features, **fields)


def enroll_new(harness, role, name, address):
    """A PC on installer 0.7.0: it declares clip_preview and upload_clip."""
    worker = harness.enroll(role, name, tailnet_address=address)
    beat(harness, worker, tailnet_address=address)
    return worker


def ready_job(harness, holder, **review):
    """A job whose raw `holder` holds, reviewed and ready to render."""
    job_id = receive_job(harness)
    hold_raw(harness, holder, job_id)
    make_proxy(harness, holder, job_id)
    review_to_ready(harness, job_id, **review)
    return job_id


def render_to_outbox(harness, renderer, job_id, clip=SAMPLE_CLIP) -> dict:
    """`renderer` claims the render and uploads the clip to the VPS outbox.
    Returns the process task it was given."""
    lease_id, task = claim_ok(harness, renderer, job_id, TaskKind.PROCESS)
    response = upload_in_chunks(harness, renderer, f"/jobs/{job_id}/clip", lease_id, clip)
    assert response.status_code == 200 and response.json()["complete"], response.text
    return task


def render_to_studio(harness, renderer, studio, job_id, clip=SAMPLE_CLIP) -> dict:
    """`renderer` claims the render and pushes it to the studio, which ACKs.
    Returns the process task."""
    _, task = claim_ok(harness, renderer, job_id, TaskKind.PROCESS)
    response = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={
            "sha256": sha256_of(clip),
            "size_bytes": len(clip),
            "relative_path": task["clip_relative_path"],
        },
        headers=studio.headers,
    )
    assert response.status_code == 200, response.text
    return task


def send_preview(harness, worker, job_id, clip_sha256, data=SAMPLE_PREVIEW, chunk_size=1000):
    """POST the preview in chunks, as worker.tasks.clip_preview does (no lease).
    Returns the last answer (complete, or the first refusal)."""
    offset = 0
    while True:
        response = harness.client.post(
            f"/jobs/{job_id}/clip-preview",
            content=data[offset : offset + chunk_size],
            headers={
                **worker.headers,
                UPLOAD_OFFSET_HEADER: str(offset),
                UPLOAD_LENGTH_HEADER: str(len(data)),
                UPLOAD_SHA256_HEADER: sha256_of(data),
                CLIP_SHA256_HEADER: clip_sha256,
            },
        )
        if response.status_code != 200 or response.json()["complete"]:
            return response
        offset = response.json()["received_bytes"]


def reprocess(harness, job_id, **fields):
    """A reviewer changes the cut and asks for a new render ("Salvar e reprocessar")."""
    job = harness.job(job_id)
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "reprocess", **fields},
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 200, response.text


def master_volunteer_studio(harness):
    """The usual event fleet, all on 0.7.0."""
    master = enroll_new(harness, WorkerRole.MASTER, "master", "100.70.0.1")
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    studio = enroll_new(harness, WorkerRole.STUDIO, "studio", "100.70.0.3")
    return master, volunteer, studio
