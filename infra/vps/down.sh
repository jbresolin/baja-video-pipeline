#!/usr/bin/env bash
# Take the pipeline down after an event (README §6). Safe to run again: a
# step with nothing left to do says so and moves on.
#
#   cd /opt/baja/infra/vps
#   sudo ./down.sh
#
# Steps:
#   1. disable the Evolution instance webhook, so Evolution stops posting to
#      a stopped orchestrator (a webhook someone pointed elsewhere is left alone);
#   2. remove /srv/caddy/conf.d/pipeline.caddy and reload the base Caddy;
#   3. docker compose down. Volumes and /srv/pipeline are kept: the jobs DB,
#      reviewers.txt and teams.csv are all there for the next up.sh.
#
# Every step runs even if an earlier one failed, so the containers always
# stop. Exit codes: 0 = all done; 1 = down, but a step needs a look (read
# the messages); 2 = could not start (no .env).

set -uo pipefail

# shellcheck source=SCRIPTDIR/stack_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/stack_lib.sh"

PROBLEMS=0

warn() {
    log "WARNING: $*"
    PROBLEMS=$((PROBLEMS + 1))
}

disable_webhook() {
    local line action message
    if ! line="$(webhook_action down)"; then
        warn "${line#*	}; the webhook may still be enabled (README §8)"
        return
    fi
    action="${line%%	*}"
    message="${line#*	}"
    log "webhook: $message"
    if [[ "$action" == "disable" ]]; then
        webhook_set false || warn "could not disable the Evolution webhook (README §8)"
    fi
}

remove_caddy_routes() {
    if [[ ! -e "$CONFD_FILE" ]]; then
        log "$CONFD_FILE already gone"
        return
    fi
    rm -f "$CONFD_FILE" || {
        warn "cannot remove $CONFD_FILE"
        return
    }
    log "removed $CONFD_FILE"
    if ! base_caddy_running; then
        log "base Caddy is not running; it will start without the pipeline routes"
        return
    fi
    if reload_base_caddy; then
        log "base Caddy reloaded"
    else
        warn "base Caddy reload failed; it keeps serving the old routes (502 for $PIPELINE_DOMAIN)"
    fi
}

stop_containers() {
    if "${COMPOSE[@]}" down --remove-orphans; then
        log "containers stopped and removed (volumes and /srv/pipeline kept)"
    else
        warn "docker compose down failed"
    fi
}

main() {
    load_env PIPELINE_DOMAIN EVOLUTION_URL BVP_EVOLUTION_INSTANCE BVP_EVOLUTION_API_KEY
    disable_webhook
    remove_caddy_routes
    stop_containers
    if ((PROBLEMS > 0)); then
        log "down, with $PROBLEMS problem(s) above"
        exit 1
    fi
    log "down"
}

main "$@"
