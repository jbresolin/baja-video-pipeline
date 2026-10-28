# Shared by up.sh and down.sh (sourced, not run). README §6.
#
# Overrides (used by the tests; you normally set none of these):
#   STACK_ENV_FILE      default: .env next to this file
#   STACK_CONFD_DIR     default: /srv/caddy/conf.d (the base Caddy imports *.caddy here)
#   STACK_PYTHON        default: python3
#   STACK_SMOKE         default: compose-smoke.sh next to this file
#   STACK_WAIT_SECONDS  default: 90 (how long up.sh waits for /health)
#   STACK_POLL_SECONDS  default: 2

# shellcheck shell=bash
# Most variables below are used by the scripts that source this file.
# shellcheck disable=SC2034

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${STACK_ENV_FILE:-$SCRIPT_DIR/.env}"
CONFD_DIR="${STACK_CONFD_DIR:-/srv/caddy/conf.d}"
PYTHON="${STACK_PYTHON:-python3}"
SMOKE="${STACK_SMOKE:-$SCRIPT_DIR/compose-smoke.sh}"
WAIT_SECONDS="${STACK_WAIT_SECONDS:-90}"
POLL_SECONDS="${STACK_POLL_SECONDS:-2}"
HELPER="$SCRIPT_DIR/stack_ctl.py"

# The base stack's names (README §1).
BASE_NETWORK=baja-edge
BASE_CADDY=caddy
BASE_CADDY_CONFIG=/etc/caddy/Caddyfile

CONFD_TEMPLATE="$SCRIPT_DIR/conf.d/pipeline.caddy"
CONFD_FILE="$CONFD_DIR/pipeline.caddy"
# Where Evolution posts: the orchestrator's alias on baja-edge (docker-compose.yml).
WEBHOOK_URL="http://bvp-orchestrator:8000/intake/evolution-webhook"
COMPOSE=(docker compose -f "$SCRIPT_DIR/docker-compose.yml")

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

log() { echo "$(basename "$0"): $*"; }
die() {
    echo "$(basename "$0"): $*" >&2
    exit 2
}

# load_env NAME...: read .env and insist the named variables are set.
load_env() {
    [[ -r "$ENV_FILE" ]] || die "cannot read $ENV_FILE (run with sudo, from infra/vps)"
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    local name
    for name in "$@"; do
        [[ -n "${!name:-}" ]] || die "$name is not set in $ENV_FILE"
    done
    CADDY_PIPELINE_DIR="${CADDY_PIPELINE_DIR:-/srv/pipeline}"
    export CADDY_PIPELINE_DIR
}

# fetch <curl args...>: body goes to $BODY, the HTTP status is printed
# ("000" when curl could not connect at all).
fetch() {
    : >"$BODY"
    curl -sS -m 15 -o "$BODY" -w '%{http_code}' "$@" 2>/dev/null || true
}

base_network_present() { docker network inspect "$BASE_NETWORK" >/dev/null 2>&1; }

base_caddy_running() {
    [[ "$(docker inspect -f '{{.State.Running}}' "$BASE_CADDY" 2>/dev/null)" == "true" ]]
}

reload_base_caddy() {
    docker exec -w /etc/caddy "$BASE_CADDY" caddy reload --config "$BASE_CADDY_CONFIG"
}

# --- Evolution instance webhook (README §8) ---------------------------------

# webhook_action up|down: ask Evolution for the current webhook and print
# "<action><TAB><message>" (stack_ctl.py decides). Returns 1 if Evolution
# could not be asked.
webhook_action() {
    local mode="$1" status
    status="$(fetch -H "apikey: $BVP_EVOLUTION_API_KEY" \
        "$EVOLUTION_URL/webhook/find/$BVP_EVOLUTION_INSTANCE")"
    if [[ "$status" != "200" ]]; then
        echo "error	Evolution answered HTTP $status to webhook/find (EVOLUTION_URL, instance name or token wrong?)"
        return 1
    fi
    "$PYTHON" "$HELPER" webhook-action --mode "$mode" --url "$WEBHOOK_URL" <"$BODY"
}

# webhook_set true|false: POST /webhook/set. The body goes through a file,
# so the secret never shows up in `ps`.
webhook_set() {
    local enabled="$1" request status
    request="$(mktemp)"
    "$PYTHON" "$HELPER" webhook-body --enabled "$enabled" --url "$WEBHOOK_URL" >"$request" || {
        rm -f "$request"
        return 1
    }
    status="$(fetch -X POST -H "apikey: $BVP_EVOLUTION_API_KEY" \
        -H 'Content-Type: application/json' --data-binary "@$request" \
        "$EVOLUTION_URL/webhook/set/$BVP_EVOLUTION_INSTANCE")"
    rm -f "$request"
    if [[ "$status" != 2?? ]]; then
        log "webhook/set answered HTTP $status: $(head -c 300 "$BODY")"
        return 1
    fi
}
