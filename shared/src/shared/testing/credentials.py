"""Every username, password, token and key the tests use, in one place (design §14).

They are placeholders, named and valued so that nobody, and no secret
scanner, can mistake one for a real credential. Tests import them from here
instead of writing literals: tests/tools/test_no_inline_credentials.py
fails on any `password="..."` / `"password": "..."` literal under tests/.
`.gitguardian.yaml` tells ggshield to ignore this module and tests/.

Usernames are not secrets; they stay readable names ("ana", "joao")
because tests and sample data assert on them (Job.reviewed_by, audit actors).
"""

# --- Review UI users (orchestrator harness, imported from its reviewers.txt) ---

REVIEWER_USERNAME = "ana"
ADMIN_USERNAME = "joao"
# Users that tests create on the admin Users page.
OTHER_USERNAME = "bruno"
THIRD_USERNAME = "carla"

PLACEHOLDER_PASSWORD = "placeholder-password-for-tests"
PLACEHOLDER_ADMIN_PASSWORD = "placeholder-admin-password-for-tests"
# For users a test creates, and for a password an admin sets later.
PLACEHOLDER_OTHER_PASSWORD = "placeholder-other-password-for-tests"
PLACEHOLDER_NEW_PASSWORD = "placeholder-new-password-for-tests"
# Deliberately not the password: sign-ins with it must fail.
PLACEHOLDER_WRONG_PASSWORD = "placeholder-wrong-password-for-tests"
# Under the 8-character minimum, so the admin page must refuse it.
PLACEHOLDER_SHORT_PASSWORD = "short"

# --- Tokens and keys ---

PLACEHOLDER_ADMIN_TOKEN = "placeholder-admin-token-for-tests"
PLACEHOLDER_WEBHOOK_SECRET = "placeholder-webhook-secret-for-tests"
PLACEHOLDER_EVOLUTION_API_KEY = "placeholder-evolution-key-for-tests"
PLACEHOLDER_TAILSCALE_CLIENT_SECRET = "placeholder-tailscale-secret-for-tests"
# A review UI session token, as the fake orchestrator issues it.
PLACEHOLDER_SESSION_TOKEN = "placeholder-session-token-for-tests"
# A worker's bearer token and an enrollment token (worker and installer tests).
PLACEHOLDER_WORKER_TOKEN = "placeholder-worker-token-for-tests"
PLACEHOLDER_ENROLLMENT_TOKEN = "placeholder-enrollment-token-for-tests"
