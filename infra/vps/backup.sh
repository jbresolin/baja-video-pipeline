#!/usr/bin/env bash
# Nightly backup of the pipeline's state (README §12, restore: restore.md).
#
#   sudo /opt/baja/infra/vps/backup.sh                     # what the systemd timer runs
#   /opt/baja/infra/vps/backup.sh --pipeline-present       # exit 0 if the orchestrator
#                                                          # container exists, else 1
#
# Writes /var/backups/baja/<UTC timestamp>/ with:
#   bvp.sqlite3              orchestrator jobs DB (SQLite online backup, integrity-checked)
#   reviewers.txt, teams.csv the two files the orchestrator and the base Caddy read
#   SHA256SUMS
# NOT included: .env and orchestrator.env (secrets; keep them in the password
# manager), raws/proxies/clips (the master worker holds every raw, design §11),
# and Evolution's stores: the base stack (jbresolin/baja-vps) backs those up.
#
# The timer only runs this while the orchestrator container exists
# (`--pipeline-present` is the unit's ExecCondition), so nights between
# events, after down.sh, are skipped instead of copying an idle DB.
#
# The folder is mode 700 (reviewer password hashes). A part that fails does
# not stop the others; the folder is then named <timestamp>-incomplete and
# the script exits 1 (the systemd unit shows "failed"). The newest
# $BAJA_BACKUP_KEEP folders are kept.
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
# docker-compose.yml's project name.
COMPOSE_PROJECT=baja-pipeline

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

write_checksums() {
    # Computed first, written after, so the list never includes itself.
    local sums
    sums="$(cd "$WORK" && find . -maxdepth 1 -type f -printf '%f\n' | sort | xargs -r sha256sum)"
    printf '%s\n' "$sums" >"$WORK/SHA256SUMS"
}

# --- the timer's condition ----------------------------------------------------

pipeline_present() {
    # Running or stopped, as long as up.sh created it and down.sh has not
    # removed it.
    local ids
    ids="$(docker ps -aq \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
        --filter "label=com.docker.compose.service=orchestrator")" || return 1
    [[ -n "$ids" ]]
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
    if [[ "${1:-}" == "--pipeline-present" ]]; then
        if pipeline_present; then
            log "orchestrator container present"
            exit 0
        fi
        log "no orchestrator container (pipeline is down); skipping"
        exit 1
    fi

    install -d -m 700 "$BACKUP_ROOT" "$WORK" || exit 2
    log "writing $WORK"

    run_part "sqlite" backup_sqlite
    run_part "pipeline files" backup_pipeline_files

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
