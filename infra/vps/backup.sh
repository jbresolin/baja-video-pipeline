#!/usr/bin/env bash
# Nightly backup of the Baja VPS state (README §13, restore: restore.md).
#
#   sudo /opt/baja/infra/vps/backup.sh          # what the systemd timer runs
#
# Writes /var/backups/baja/<UTC timestamp>/ with:
#   bvp.sqlite3              orchestrator jobs DB (SQLite online backup, integrity-checked)
#   reviewers.txt, teams.csv the two files the orchestrator and Caddy read
#   evolution.dump           Evolution's Postgres DB (pg_dump -Fc, as in README §3.2a)
#   evolution.rdb            Redis snapshot (the WhatsApp Signal keys, README §3.2b)
#   evolution_instances.tgz  the Evolution instances volume (README §3.2c)
#   SHA256SUMS
# NOT included: .env and orchestrator.env (secrets; keep them in the password
# manager), raws/proxies/clips (the master worker holds every raw, design §11).
#
# The folder is mode 700: the Evolution files contain the WhatsApp session
# keys. A part that fails does not stop the others; the folder is then
# named <timestamp>-incomplete and the script exits 1 (the systemd unit
# shows "failed"). The newest $BAJA_BACKUP_KEEP folders are kept.
#
# Overrides (tests; normally none): BAJA_BACKUP_ROOT, BAJA_BACKUP_KEEP,
# BAJA_DATA_DIR, BAJA_BACKUP_UID, BAJA_BACKUP_PYTHON.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${BAJA_BACKUP_ROOT:-/var/backups/baja}"
KEEP="${BAJA_BACKUP_KEEP:-14}"
DATA_DIR="${BAJA_DATA_DIR:-/srv/pipeline}"
# The orchestrator's user (design §17); SQLite is opened as this user.
DATA_UID="${BAJA_BACKUP_UID:-10001}"
PYTHON="${BAJA_BACKUP_PYTHON:-python3}"
COMPOSE=(docker compose -f "$SCRIPT_DIR/docker-compose.yml" --profile orchestrator)

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
WORK="$BACKUP_ROOT/.$STAMP.partial"
FAILED_PARTS=()

log() { echo "backup: $*"; }

# run_part <name> <function>: run one part, remember it if it failed.
run_part() {
    local name="$1" function="$2"
    if "$function"; then
        log "$name ok"
    else
        log "$name FAILED"
        FAILED_PARTS+=("$name")
    fi
}

# --- parts --------------------------------------------------------------------

backup_sqlite() {
    # A scratch folder the orchestrator's user may write into.
    local staging
    staging="$(mktemp -d)" || return 1
    chown "$DATA_UID:$DATA_UID" "$staging" || return 1
    setpriv --reuid "$DATA_UID" --regid "$DATA_UID" --clear-groups -- \
        "$PYTHON" "$SCRIPT_DIR/sqlite_backup.py" backup \
        "$DATA_DIR/db/bvp.sqlite3" "$staging/bvp.sqlite3" &&
        mv "$staging/bvp.sqlite3" "$WORK/bvp.sqlite3"
    local status=$?
    rm -rf "$staging"
    return $status
}

backup_pipeline_files() {
    cp -p "$DATA_DIR/reviewers.txt" "$WORK/reviewers.txt" &&
        cp -p "$DATA_DIR/teams.csv" "$WORK/teams.csv"
}

backup_postgres() {
    # Same dump as README §3.2a, against the VPS container, streamed out.
    # The single quotes are on purpose: $POSTGRES_* expand inside the container.
    # shellcheck disable=SC2016
    "${COMPOSE[@]}" exec -T postgres sh -c \
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-privileges' \
        >"$WORK/evolution.dump" && [[ -s "$WORK/evolution.dump" ]]
}

backup_redis() {
    # README §3.2b: a consistent RDB over the replication protocol; Redis keeps running.
    "${COMPOSE[@]}" exec -T redis redis-cli --rdb /tmp/backup.rdb >/dev/null &&
        "${COMPOSE[@]}" cp redis:/tmp/backup.rdb "$WORK/evolution.rdb" >/dev/null &&
        "${COMPOSE[@]}" exec -T redis rm -f /tmp/backup.rdb &&
        [[ -s "$WORK/evolution.rdb" ]]
}

backup_evolution_instances() {
    # README §3.2c. Read-only mount of the named volume into a throwaway container.
    docker run --rm -v baja_evolution_instances:/instances:ro alpine:3.22 \
        tar -C /instances -czf - . >"$WORK/evolution_instances.tgz"
}

write_checksums() {
    # Computed first, written after, so the list never includes itself.
    local sums
    sums="$(cd "$WORK" && find . -maxdepth 1 -type f -printf '%f\n' | sort | xargs -r sha256sum)"
    printf '%s\n' "$sums" >"$WORK/SHA256SUMS"
}

# --- retention ----------------------------------------------------------------

prune_old_backups() {
    # Folder names start with the UTC timestamp, so name order = age order.
    local folders=() folder
    mapfile -t folders < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name '20*' -printf '%f\n' | sort -r)
    local index
    for ((index = KEEP; index < ${#folders[@]}; index++)); do
        folder="$BACKUP_ROOT/${folders[index]}"
        log "removing old backup $folder"
        rm -rf -- "$folder"
    done
}

# --- main ---------------------------------------------------------------------

main() {
    install -d -m 700 "$BACKUP_ROOT" "$WORK" || exit 2
    log "writing $WORK"

    run_part "sqlite" backup_sqlite
    run_part "pipeline files" backup_pipeline_files
    run_part "postgres" backup_postgres
    run_part "redis" backup_redis
    run_part "evolution instances" backup_evolution_instances

    write_checksums

    local final="$BACKUP_ROOT/$STAMP"
    if ((${#FAILED_PARTS[@]} > 0)); then
        final="$final-incomplete"
    fi
    mv "$WORK" "$final"
    prune_old_backups

    if ((${#FAILED_PARTS[@]} > 0)); then
        log "INCOMPLETE: $final (failed: ${FAILED_PARTS[*]})"
        exit 1
    fi
    log "done: $final ($(du -sh "$final" | cut -f1))"
}

main "$@"
