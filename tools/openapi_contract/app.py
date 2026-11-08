"""Assemble the contract routers into one FastAPI app (for its OpenAPI document)."""

from fastapi import FastAPI

from tools.openapi_contract import (
    routes_admin_catalog,
    routes_admin_operations,
    routes_admin_people,
    routes_auth,
    routes_intake_enrollment,
    routes_metrics,
    routes_review_delivery,
    routes_worker_loop,
)

# 2.0.0: reviewerBasic replaced by sessionBearer (POST /auth/login) and the
# admin routes added (admin page, LIVESTACK-37).
# 2.1.0: media info, quoted bot replies, restore, the clip archive and the
# worker's paused flag (first real video, LIVESTACK-38).
# 2.2.0: worker display name and host name, "Tornar master" with the role in
# heartbeats, the clip name choices (admin polish from real use, LIVESTACK-42).
# 2.3.0: shared_tailnet, readable file names, "Baixar original" with the
# upload_original task and the originals settings (LIVESTACK-41).
# 2.4.0: render inputs per clip version (PUT /jobs/{id}/render-inputs,
# Job.clip_inputs, ManifestEntry.clip_inputs, JobSummary.clip_outdated,
# GET /review/queue?outdated=true); a reprocess is delivered under its -vN
# name (edits after delivery, LIVESTACK-43). Answer schemas no longer say
# additionalProperties: false: clients ignore fields they do not know.
# 2.5.0: Job/JobSummary.recorded_at and recorded_at_source, IntakeSource.sent_at
# and received_at, the review queue in filming order, GET /review/car-videos
# (help reviewers number the passagens, LIVESTACK-45); the welcome message for
# new contacts (AdminSettings.welcome_message, CreateContactRequest.send_welcome
# → ContactCreated.welcome, POST /admin/contacts/{phone}/welcome,
# Contact.welcome_sent_at, ContactsResponse.welcome_message_set; LIVESTACK-44).
# 2.6.0: render options (LIVESTACK-50): Job.rotation/framing/crop_position/
# no_audio (also on ReviewUpdate and ProcessTask), Job.display_width/height and
# vertical, JobSummary.vertical, RenderInputs/RenderInputsReport options and
# their RenderInputField values; render features (WorkerCapabilities.
# render_features, HeartbeatRequest.render_features, ProcessTask.
# required_render_features); AdminSettings.vertical_video_message.
# Deploy the VPS first: an older orchestrator refuses the new heartbeat and
# render-inputs fields (the worker falls back for heartbeats).
# Also in 2.6.0, the sender's messages for the reviewer (GET /jobs/{id}/sender-messages,
# IntakeSource.caption, JobSummary.received_at and sender_message_count,
# AdminSettings.sender_messages_keep_days, IntakeResponse.message_stored; the
# webhook also reads messages.edited and messages.delete; LIVESTACK-49).
# 2.6.1: worker-read answers accept values a client does not know yet
# (HeartbeatResponse.lease_status values and role, JobStateResponse.state:
# the enum or any string), so an older worker can still read them (LIVESTACK-46).
# 2.7.0: official pass results in the overlay (LIVESTACK-47): ProcessTask.
# pass_result_text and the render feature "pass_result"; Job.show_pass_result
# (also on ReviewUpdate) and Job.pass_result; RenderInputs/RenderInputsReport.
# pass_result_text and RenderInputField "pass_result"; GET /admin/pass-results,
# PUT /admin/pass-results/config, POST /admin/pass-results/test. Deploy the
# VPS first: an older orchestrator refuses every render-inputs report of a
# 0.5.0 worker (it always carries the pass_result_text key, null or not); the
# worker logs it and delivers anyway, and the version keeps the claim snapshot.
# 2.8.0: the running clock in the overlay (LIVESTACK-48): ProcessTask.pass_clock
# (RenderClock) and the render feature "clock"; Job.largada_s, show_clock and
# pass_clock (PassClock); ReviewUpdate.largada_s, clear_largada and show_clock;
# RenderInputs.pass_clock, RenderInputsReport.pass_clock (ReportedClock) and
# RenderInputField "clock"; PassResultTestResponse.clock_*. Deploy the VPS
# first: an older orchestrator refuses every render-inputs report of a 0.6.0
# worker (it always carries the pass_clock key); the worker logs it and
# delivers anyway, and the version keeps the claim snapshot.
# 2.9.0: review fixes from the 2026-11-07 test (LIVESTACK-51): JobSummary.
# sender_phone and sender_display_name (the queue's "Enviado por" filter);
# duplicate videos: Job.duplicate_of and duplicates (DuplicateCopy),
# JobSummary.duplicate_of, AdminSettings.duplicate_video_message.
# Who has a job page open: PUT/DELETE /jobs/{id}/presence (JobPresenceResponse,
# PresentReviewer) and JobSummary.reviewing.
# One contact per phone: Contact.waiting_without_prova and
# UpdateContactRequest.apply_default_prova_to_waiting; a {phone} in the path
# may be written in any form (9th digit, +55).
# Only reviewer-facing answers changed; workers ignore them (§14).
# 2.10.0: clock polish (LIVESTACK-53): GET /jobs/{id}/pass-preview
# (PassPreview), the result line and the clock for values not saved yet.
# The clock's unit ("66,0 s", the configuration's clock.unit, default "s"):
# RenderClock.unit and ReportedClock.unit (null = recorded before 2.10.0,
# never compared), PassClock.unit, PassResultTestResponse.clock_unit.
# Deploy the VPS first: an older orchestrator refuses a report whose
# pass_clock carries `unit`; the worker logs it and delivers anyway.
CONTRACT_VERSION = "2.10.0"

DESCRIPTION = """\
Orchestrator API of the Baja video pipeline v2. Generated from
tools/openapi_contract (FastAPI stubs over shared.models); prose,
flows and the chunked upload protocol are in docs/api.md.
Do not edit this file by hand: run `uv run python tools/generate_openapi.py`.
"""


def build_contract_app() -> FastAPI:
    app = FastAPI(
        title="Baja video pipeline — orchestrator API",
        version=CONTRACT_VERSION,
        description=DESCRIPTION,
    )
    app.include_router(routes_intake_enrollment.router)
    app.include_router(routes_worker_loop.router)
    app.include_router(routes_review_delivery.router)
    app.include_router(routes_metrics.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_admin_people.router)
    app.include_router(routes_admin_catalog.router)
    app.include_router(routes_admin_operations.router)
    return app
