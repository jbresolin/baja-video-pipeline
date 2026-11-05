"""Admin Enrollment, Workers and Settings pages.

* Enrollment: issue a token (shown once, with the installer link), list
  tokens with their status, revoke unused ones.
* Workers: revoke a worker. Its bearer token stops working at once, its
  leases end and it stops counting as a holder.
* Settings: intake mode, inbox soft cap, proxy eviction threshold and
  installer URL live in the database; .env only gave the first values.
  Secrets are never shown, only "configured" or "missing".
"""

from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import claim_ok, receive_job
from shared.models import TaskKind, WorkerRole


def issue_token(harness, name="Maria", role="worker", hours=72):
    response = harness.client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": name, "role": role, "valid_for_hours": hours},
        auth=harness.admin_auth,
    )
    assert response.status_code == 201, response.text
    return response.json()


def tokens(harness) -> dict[str, dict]:
    listed = harness.client.get("/admin/enrollment-tokens", auth=harness.admin_auth).json()
    return {token["token_id"]: token for token in listed["tokens"]}


def enroll_with(harness, token):
    return harness.client.post(
        "/enroll",
        json={
            "enrollment_token": token,
            "worker_name": "pc-da-maria",
            "capabilities": {"has_nvenc": False, "video_encoder": "libx264"},
            "storage_cap_bytes": 100_000_000_000,
        },
    )


# --- Enrollment tokens ---


def test_a_new_token_comes_with_its_id_and_the_installer_link(harness):
    created = issue_token(harness)
    # The installer link is the Settings value (default: the releases page).
    assert created["installer_download_url"].startswith("https://github.com/")
    # The list shows it as unused, by id; the token itself is never listed.
    listed = tokens(harness)[created["token_id"]]
    assert listed["status"] == "unused"
    assert created["token"] not in str(tokens(harness))


def test_the_list_says_used_by_whom_expired_and_revoked(harness):
    used = issue_token(harness, "Used")
    expiring = issue_token(harness, "Expiring", hours=1)
    revoked = issue_token(harness, "Revoked")
    worker_id = enroll_with(harness, used["token"]).json()["worker_id"]
    harness.client.post(
        f"/admin/enrollment-tokens/{revoked['token_id']}/revoke", auth=harness.admin_auth
    )
    harness.clock.advance(2 * 3600)  # the 1-hour token is now past its time

    listed = tokens(harness)
    assert listed[used["token_id"]]["status"] == "used"
    assert listed[used["token_id"]]["used_by_worker_id"] == worker_id
    assert listed[used["token_id"]]["used_by_worker_name"] == "pc-da-maria"
    assert listed[expiring["token_id"]]["status"] == "expired"
    assert listed[revoked["token_id"]]["status"] == "revoked"


def test_a_revoked_token_cannot_be_used_and_a_used_one_cannot_be_revoked(harness):
    revoked = issue_token(harness)
    harness.client.post(
        f"/admin/enrollment-tokens/{revoked['token_id']}/revoke", auth=harness.admin_auth
    )
    refused = enroll_with(harness, revoked["token"])
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        401,
        "enrollment_token_invalid",
    )
    used = issue_token(harness)
    enroll_with(harness, used["token"])
    too_late = harness.client.post(
        f"/admin/enrollment-tokens/{used['token_id']}/revoke", auth=harness.admin_auth
    )
    assert (too_late.status_code, too_late.json()["error"]["code"]) == (409, "conflict")
    unknown = harness.client.post(
        "/admin/enrollment-tokens/ffffffffffffffff/revoke", auth=harness.admin_auth
    )
    assert unknown.status_code == 404


# --- Workers ---


def test_revoking_a_worker_stops_its_token_ends_its_leases_and_drops_it_as_holder(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness)
    # The volunteer starts downloading the raw (a lease, and an assigned holder).
    claim_ok(harness, volunteer, job_id, TaskKind.DOWNLOAD_RAW)

    revoked = harness.client.post(
        f"/admin/workers/{volunteer.worker_id}/revoke", auth=harness.admin_auth
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    # Its token no longer works anywhere.
    heartbeat = harness.client.post(
        f"/workers/{volunteer.worker_id}/heartbeat",
        json={"free_bytes": 1, "storage_cap_bytes": 1},
        headers=volunteer.headers,
    )
    assert heartbeat.status_code == 401
    # Its lease ended, and it is no longer a holder of that raw.
    job = harness.job(job_id)
    assert all(holder["worker_id"] != volunteer.worker_id for holder in job["holders"])
    assert all(lease["worker_id"] != volunteer.worker_id for lease in job["leases"])
    # The fleet shows it revoked and offline; the master is untouched.
    fleet = harness.client.get("/fleet", auth=harness.admin_auth).json()["workers"]
    by_id = {entry["worker"]["id"]: entry for entry in fleet}
    assert by_id[volunteer.worker_id]["online"] is False
    assert by_id[volunteer.worker_id]["worker"]["revoked_at"] is not None
    assert by_id[master.worker_id]["online"] is True


def test_worker_revocation_is_audited_and_unknown_workers_are_404(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    harness.client.post(f"/admin/workers/{worker.worker_id}/revoke", auth=harness.admin_auth)
    [event] = harness.client.get("/admin/audit?area=workers", auth=harness.admin_auth).json()[
        "events"
    ]
    assert event["kind"] == "worker_revoked"
    assert event["worker_id"] == worker.worker_id
    missing = harness.client.post(f"/admin/workers/{'0' * 32}/revoke", auth=harness.admin_auth)
    assert missing.status_code == 404


# --- Settings ---


def settings_page(harness) -> dict:
    response = harness.client.get("/admin/settings", auth=harness.admin_auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_start_from_the_environment_values(tmp_path):
    harness = build_harness(
        tmp_path / "data", inbox_hold_gb=20, intake_mode="allowlist", proxy_evict_min_free_gb=5
    )
    values = settings_page(harness)["settings"]
    assert values["intake_mode"] == "allowlist"
    assert (values["inbox_hold_gb"], values["proxy_evict_min_free_gb"]) == (20, 5)


def test_after_the_first_start_the_database_wins_over_the_environment(tmp_path):
    # Change a setting in the UI, then "restart" with a different .env.
    first = build_harness(tmp_path / "data", inbox_hold_gb=20)
    first.client.patch("/admin/settings", json={"inbox_hold_gb": 15}, auth=first.admin_auth)
    first.context.database.close()
    restarted = build_harness(tmp_path / "data", inbox_hold_gb=21)
    assert settings_page(restarted)["settings"]["inbox_hold_gb"] == 15


def test_secrets_are_only_reported_as_configured_or_missing(tmp_path):
    harness = build_harness(tmp_path / "data", tailscale_oauth_client_secret="")
    page = settings_page(harness)
    assert page["status"]["secrets"] == {
        "BVP_ADMIN_TOKEN": "configured",
        "BVP_WEBHOOK_SECRET": "configured",
        "BVP_EVOLUTION_API_KEY": "configured",
        "TAILSCALE_OAUTH_CLIENT_SECRET": "missing",
    }
    # No secret value appears anywhere in the answer.
    for secret in ("test-admin-token", "test-webhook-secret", "fake-evolution-key"):
        assert secret not in str(page)


def test_the_status_block_shows_evolution_and_disk(harness):
    harness.evolution.connection_state = "connecting"
    status = settings_page(harness)["status"]
    assert status["evolution"]["state"] == "connecting"
    assert status["evolution"]["webhook_enabled"] is True
    assert status["evolution"]["webhook_url"].endswith("/intake/evolution-webhook")
    assert status["inbox_cap_gb"] == 25
    assert status["server_disk"]["inbox_hold_bytes"] == 22_000_000_000


def test_the_inbox_soft_cap_cannot_go_above_the_hard_cap(harness):
    response = harness.client.patch(
        "/admin/settings", json={"inbox_hold_gb": 30}, auth=harness.admin_auth
    )
    assert response.status_code == 400
    bad_url = harness.client.patch(
        "/admin/settings", json={"installer_download_url": "ftp://x"}, auth=harness.admin_auth
    )
    assert bad_url.status_code == 400


def test_a_changed_setting_takes_effect_at_once_and_is_audited(harness):
    harness.client.patch(
        "/admin/settings",
        json={"inbox_hold_gb": 10, "installer_download_url": "https://example.org/Setup.exe"},
        auth=harness.admin_auth,
    )
    # The fleet page's disk block and new tokens use the new values.
    fleet = harness.client.get("/fleet", auth=harness.admin_auth).json()
    assert fleet["server_disk"]["inbox_hold_bytes"] == 10_000_000_000
    assert issue_token(harness)["installer_download_url"] == "https://example.org/Setup.exe"
    [event] = harness.client.get("/admin/audit?area=settings", auth=harness.admin_auth).json()[
        "events"
    ]
    assert event["details"]["before"] == {
        "inbox_hold_gb": 22.0,
        "installer_download_url": "https://github.com/jbresolin/baja-video-pipeline/releases/latest",
    }
    assert event["details"]["after"]["inbox_hold_gb"] == 10.0
