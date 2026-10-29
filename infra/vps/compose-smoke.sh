#!/usr/bin/env bash
# Smoke test for the pipeline stack. up.sh runs it last; run it by hand
# any time:
#
#   cd /opt/baja/infra/vps
#   sudo ./compose-smoke.sh
#
# It checks, and prints as a PASS/FAIL table (exit code 1 if anything fails):
#   * the base stack is present: network baja-edge, container caddy running,
#     and BVP_TRUSTED_PROXY_CIDRS covers baja-edge (README §9);
#   * up.sh's routes are installed in the base Caddy's conf.d;
#   * the pipeline's containers are running, and healthy where they have a
#     healthcheck;
#   * the base Caddy answers on 443 for PIPELINE_DOMAIN, and pipeline-edge on
#     the Tailscale IP:8080;
#   * the orchestrator answers through both: /health on the tailnet side,
#     POST /enroll on the public side (the only public orchestrator route,
#     design §8; an empty body must come back 400 from the orchestrator);
#   * /metrics answers with the admin token;
#   * the Evolution instance is connected (state "open") and its webhook
#     points at the orchestrator;
#   * /srv/pipeline/reviewers.txt parses (same rules as the orchestrator);
#   * the data folders and files belong to uid 10001 and the inbox is its
#     own filesystem (README §2.1, §2.3).
#
# Needs root (it reads .env and the reviewers file). Changes nothing.
# Each check_* function only gathers command output; smoke_checks.py next
# to this script decides PASS/FAIL (tested in tests/infra/).
#
# Overrides (used by the tests; you normally set none of these):
#   SMOKE_ENV_FILE   default: .env next to this script
#   SMOKE_DATA_DIR   default: /srv/pipeline
#   SMOKE_CONFD_DIR  default: /srv/caddy/conf.d
#   SMOKE_UID        default: 10001 (the orchestrator/review-ui image user)
#   SMOKE_GID        default: 10001
#   SMOKE_PYTHON     default: python3

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SMOKE_ENV_FILE:-$SCRIPT_DIR/.env}"
DATA_DIR="${SMOKE_DATA_DIR:-/srv/pipeline}"
CONFD_DIR="${SMOKE_CONFD_DIR:-/srv/caddy/conf.d}"
EXPECTED_UID="${SMOKE_UID:-10001}"
EXPECTED_GID="${SMOKE_GID:-10001}"
PYTHON="${SMOKE_PYTHON:-python3}"
HELPER="$SCRIPT_DIR/smoke_checks.py"

# Same names as stack_lib.sh (README §1).
BASE_NETWORK=baja-edge
BASE_CADDY=caddy
WEBHOOK_URL="http://bvp-orchestrator:8000/intake/evolution-webhook"

RESULTS="$(mktemp)"
BODY="$(mktemp)"
trap 'rm -f "$RESULTS" "$BODY"' EXIT

# --- helpers ------------------------------------------------------------------

# Run the Python helper and append its PASS/FAIL lines to the results.
decide() {
    "$PYTHON" "$HELPER" "$@" >>"$RESULTS"
}

# fetch <curl args...>: body goes to $BODY, the HTTP status is printed
# ("000" when curl could not connect at all).
fetch() {
    : >"$BODY"
    curl -sS -m 15 -o "$BODY" -w '%{http_code}' "$@" 2>/dev/null || true
}

load_env() {
    if [[ ! -r "$ENV_FILE" ]]; then
        echo "compose-smoke: cannot read $ENV_FILE (run with sudo, from infra/vps)" >&2
        exit 2
    fi
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    local name
    for name in PIPELINE_DOMAIN TAILSCALE_IP EVOLUTION_URL BVP_EVOLUTION_API_KEY \
        BVP_EVOLUTION_INSTANCE BVP_ADMIN_TOKEN; do
        if [[ -z "${!name:-}" ]]; then
            echo "compose-smoke: $name is not set in $ENV_FILE" >&2
            exit 2
        fi
    done
}

# --- checks -------------------------------------------------------------------

check_base_stack() {
    { docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' \
        "$BASE_NETWORK" 2>/dev/null || echo MISSING; } |
        decide base-network "$BASE_NETWORK" \
            --trusted-cidrs "${BVP_TRUSTED_PROXY_CIDRS:-127.0.0.1/32,10.201.0.0/24,10.202.0.0/24}"
    { docker inspect -f '{{.State.Status}}' "$BASE_CADDY" 2>/dev/null || echo MISSING; } |
        decide base-caddy "$BASE_CADDY"
    decide confd "$CONFD_DIR/pipeline.caddy" --domain "$PIPELINE_DOMAIN"
}

check_containers() {
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
        ps --all --format json 2>/dev/null | decide containers
}

check_caddy_public() {
    local status
    # Basic auth in front of the review UI: 401 proves TLS + Caddy + the routes.
    status="$(fetch "https://$PIPELINE_DOMAIN/review/")"
    decide http "caddy 443 $PIPELINE_DOMAIN" --got "$status" --expect 401 <"$BODY"
}

check_caddy_tailnet() {
    local status
    status="$(fetch "http://$TAILSCALE_IP:8080/health")"
    decide http "orchestrator /health via $TAILSCALE_IP:8080" \
        --got "$status" --expect 200 --json-field status=ok <"$BODY"
}

check_orchestrator_public() {
    local status
    # Empty body → the orchestrator's own 400 validation_error. (Costs one
    # of the 10 /enroll attempts per 10 minutes for the VPS's own IP.)
    status="$(fetch -X POST -H 'Content-Type: application/json' -d '{}' \
        "https://$PIPELINE_DOMAIN/enroll")"
    decide http "orchestrator via 443 (POST /enroll)" --got "$status" --expect 400 <"$BODY"
}

check_metrics() {
    local status
    status="$(fetch -H "Authorization: Bearer $BVP_ADMIN_TOKEN" \
        "http://$TAILSCALE_IP:8080/metrics")"
    decide metrics --got "$status" <"$BODY"
}

check_evolution() {
    local status
    # The instance token is enough for this instance's own routes.
    status="$(fetch -H "apikey: $BVP_EVOLUTION_API_KEY" \
        "$EVOLUTION_URL/instance/connectionState/$BVP_EVOLUTION_INSTANCE")"
    decide evolution-state --got "$status" <"$BODY"
    status="$(fetch -H "apikey: $BVP_EVOLUTION_API_KEY" \
        "$EVOLUTION_URL/webhook/find/$BVP_EVOLUTION_INSTANCE")"
    decide webhook --got "$status" --url "$WEBHOOK_URL" <"$BODY"
}

check_reviewers() {
    decide reviewers "$DATA_DIR/reviewers.txt"
}

check_disk_layout() {
    local path
    for path in "$DATA_DIR" "$DATA_DIR"/{inbox,proxies,thumbs,outbox,db} \
        "$DATA_DIR/reviewers.txt" "$DATA_DIR/teams.csv"; do
        if [[ -e "$path" ]]; then
            stat -c '%u %g %a %n' "$path"
        else
            echo "MISSING $path"
        fi
    done | decide ownership --uid "$EXPECTED_UID" --gid "$EXPECTED_GID"
    findmnt -n -b -o FSTYPE,SIZE --mountpoint "$DATA_DIR/inbox" 2>/dev/null |
        decide inbox-mount --cap-gb "${BVP_INBOX_CAP_GB:-25}"
}

# --- main ---------------------------------------------------------------------

main() {
    load_env
    check_base_stack
    check_containers
    check_caddy_public
    check_caddy_tailnet
    check_orchestrator_public
    check_metrics
    check_evolution
    check_reviewers
    check_disk_layout
    "$PYTHON" "$HELPER" report "$RESULTS"
}

main "$@"
