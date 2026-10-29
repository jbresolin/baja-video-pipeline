#!/usr/bin/env bash
# Bring the pipeline up on the VPS (README §6). Safe to run again at any
# time: every step checks first and only changes what is not already right.
#
#   cd /opt/baja/infra/vps
#   sudo ./up.sh
#
# Steps:
#   1. the base stack is there: network baja-edge, container caddy running,
#      the conf.d folder exists;
#   2. docker compose pull + up -d (orchestrator, review-ui, pipeline-edge);
#   3. wait until the orchestrator answers /health on the tailnet listener;
#   4. render conf.d/pipeline.caddy (envsubst), install it as
#      /srv/caddy/conf.d/pipeline.caddy, reload the base Caddy; a reload that
#      fails puts the previous file back, so other stacks' reloads still work;
#   5. point the Evolution instance webhook at the orchestrator, with
#      X-Webhook-Secret, unless it already is;
#   6. run compose-smoke.sh.
#
# Exit codes: 0 = up and every smoke check passed; 1 = up, but a smoke
# check failed (read the table); 2 = a step failed and up.sh stopped there.

set -uo pipefail

# shellcheck source=SCRIPTDIR/stack_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/stack_lib.sh"

check_base_stack() {
    base_network_present ||
        die "network $BASE_NETWORK not found: start the base stack (jbresolin/baja-vps) first"
    base_caddy_running ||
        die "container $BASE_CADDY is not running: start the base stack (jbresolin/baja-vps) first"
    [[ -d "$CONFD_DIR" && -w "$CONFD_DIR" ]] ||
        die "$CONFD_DIR is missing or not writable (base stack, README §1; run with sudo)"
    log "base stack present ($BASE_NETWORK, $BASE_CADDY, $CONFD_DIR)"
}

start_containers() {
    "${COMPOSE[@]}" pull || die "docker compose pull failed (ghcr login? README §5)"
    "${COMPOSE[@]}" up -d --remove-orphans || die "docker compose up failed"
}

wait_for_orchestrator() {
    local waited=0 status
    while :; do
        status="$(fetch "http://$TAILSCALE_IP:8080/health")"
        if [[ "$status" == "200" ]]; then
            log "orchestrator answers on $TAILSCALE_IP:8080"
            return 0
        fi
        if ((waited >= WAIT_SECONDS)); then
            die "no /health on $TAILSCALE_IP:8080 after ${WAIT_SECONDS}s (last HTTP $status); docker compose logs orchestrator pipeline-edge"
        fi
        sleep "$POLL_SECONDS"
        waited=$((waited + POLL_SECONDS))
        # A zero poll interval (tests) still has to end.
        ((POLL_SECONDS > 0)) || waited=$((waited + 1))
    done
}

install_caddy_routes() {
    local rendered previous=""
    rendered="$(mktemp)"
    # Only these two names are substituted; Caddy's own {placeholders} stay.
    # shellcheck disable=SC2016
    envsubst '${PIPELINE_DOMAIN} ${CADDY_PIPELINE_DIR}' <"$CONFD_TEMPLATE" >"$rendered" ||
        die "envsubst failed (apt install gettext-base)"

    if [[ -f "$CONFD_FILE" ]] && cmp -s "$rendered" "$CONFD_FILE"; then
        log "$CONFD_FILE unchanged"
    else
        if [[ -f "$CONFD_FILE" ]]; then
            previous="$(mktemp)"
            cp -p "$CONFD_FILE" "$previous"
        fi
        # Write next to it and rename, so Caddy never imports half a file.
        # The temporary name does not end in .caddy, so the glob skips it.
        if ! install -m 644 "$rendered" "$CONFD_FILE.new" ||
            ! mv -f "$CONFD_FILE.new" "$CONFD_FILE"; then
            die "cannot write $CONFD_FILE"
        fi
        log "installed $CONFD_FILE for $PIPELINE_DOMAIN"
    fi
    rm -f "$rendered"

    # Always reload: it also picks up reviewers.txt edits, and Caddy skips a
    # reload whose config did not change.
    if ! reload_base_caddy; then
        if [[ -n "$previous" ]]; then
            mv -f "$previous" "$CONFD_FILE"
            die "base Caddy refused the new routes; previous $CONFD_FILE put back (error above)"
        fi
        rm -f "$CONFD_FILE"
        die "base Caddy refused the routes; $CONFD_FILE removed again (error above; reviewers.txt empty?)"
    fi
    [[ -z "$previous" ]] || rm -f "$previous"
    log "base Caddy reloaded"
}

enable_webhook() {
    local line action message
    line="$(webhook_action up)" || die "${line#*	}"
    action="${line%%	*}"
    message="${line#*	}"
    if [[ "$action" == "ok" ]]; then
        log "webhook: $message"
        return 0
    fi
    log "webhook: $message"
    webhook_set true || die "could not set the Evolution webhook (README §8)"
    log "webhook: now $WEBHOOK_URL"
}

run_smoke() {
    log "smoke test:"
    "$SMOKE"
    local status=$?
    # The smoke test's own exit 2 (could not read .env) is a stopped step too.
    exit "$status"
}

main() {
    load_env PIPELINE_DOMAIN TAILSCALE_IP EVOLUTION_URL BVP_EVOLUTION_INSTANCE \
        BVP_EVOLUTION_API_KEY BVP_WEBHOOK_SECRET BVP_ADMIN_TOKEN
    check_base_stack
    start_containers
    wait_for_orchestrator
    install_caddy_routes
    enable_webhook
    run_smoke
}

main "$@"
