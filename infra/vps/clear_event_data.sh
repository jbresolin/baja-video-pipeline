#!/usr/bin/env bash
# Start the next event from scratch: delete every video and everything
# about it on the VPS, keeping the setup (README §15).
#
#   cd /opt/baja/infra/vps
#   sudo ./down.sh
#   sudo ./clear_event_data.sh
#   sudo ./up.sh
#
# Deleted: every job in the DB and what points at it (clear_event_data.py
# lists the tables), and the files in inbox/, proxies/, thumbs/, outbox/,
# originals/, previews/ and requested_clips/.
# Kept: users, contacts, provas, teams, Settings (with the pass-results
# config), enrolled workers, enrollment tokens and the admin history.
#
# Safety:
#   * refuses while the pipeline is up (the orchestrator would be writing
#     to the DB while rows are deleted under it): run ./down.sh first;
#   * asks you to type APAGAR (or pass --yes);
#   * first copies the DB to /var/backups/baja/before-clear-<UTC time>/, so
#     a mistake can be undone with restore.md (this copy is not rotated
#     away by the nightly backups);
#   * the DB part is one transaction: it all happens or none of it.
#
# The PCs keep their own copies (raws/, clips/, the studio's clip folder):
# README §15 says how to empty those.
#
# Overrides (tests; normally none): BAJA_BACKUP_ROOT, BAJA_DATA_DIR,
# BAJA_CLEAR_PYTHON (run the Python scripts with this local Python instead of
# in the orchestrator image; the tests have no Docker).

set -euo pipefail

# shellcheck source=SCRIPTDIR/stack_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/stack_lib.sh"

BACKUP_ROOT="${BAJA_BACKUP_ROOT:-/var/backups/baja}"
DATA_DIR="${BAJA_DATA_DIR:-/srv/pipeline}"
DATABASE="$DATA_DIR/db/bvp.sqlite3"
LOCAL_PYTHON="${BAJA_CLEAR_PYTHON:-}"
COMPOSE_PROJECT=baja-pipeline
# The folders of files that belong to jobs (orchestrator/storage.py).
JOB_FILE_FOLDERS=(inbox proxies thumbs outbox originals previews requested_clips)

pipeline_is_running() {
    [[ -n "$(docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
        --filter "label=com.docker.compose.service=orchestrator")" ]]
}

# run_in_orchestrator <action> <arguments...>: sqlite_in_orchestrator.sh, or
# the local Python in tests.
run_in_orchestrator() {
    local action="$1"
    shift
    if [[ -z "$LOCAL_PYTHON" ]]; then
        "$SCRIPT_DIR/sqlite_in_orchestrator.sh" "$action" "$@"
    elif [[ "$action" == "clear-event-data" ]]; then
        "$LOCAL_PYTHON" "$SCRIPT_DIR/clear_event_data.py" "$@"
    else
        "$LOCAL_PYTHON" "$SCRIPT_DIR/sqlite_backup.py" "$action" "$@"
    fi
}

confirm_or_stop() {
    if [[ "${1:-}" == "--yes" ]]; then
        return
    fi
    cat <<EOF
This deletes every video on the VPS: the jobs in $DATABASE and the files in
$(printf '%s/ ' "${JOB_FILE_FOLDERS[@]}")under $DATA_DIR.
It keeps users, contacts, provas, teams, Settings and enrolled workers.
A copy of the DB is saved first in $BACKUP_ROOT/before-clear-<time>/.
EOF
    local answer
    read -r -p "Type APAGAR to go on: " answer
    [[ "$answer" == "APAGAR" ]] || die "nothing was deleted"
}

[[ "${EUID:-$(id -u)}" -eq 0 || -n "$LOCAL_PYTHON" ]] || die "run with sudo"
[[ -f "$DATABASE" ]] || die "no jobs DB at $DATABASE"
if pipeline_is_running; then
    die "the pipeline is up; run sudo ./down.sh first, then this, then sudo ./up.sh"
fi
confirm_or_stop "${1:-}"

# 1. The safety copy, checked by the same SQLite that wrote the DB.
safety_folder="$BACKUP_ROOT/before-clear-$(date -u +%Y-%m-%dT%H%M%SZ)"
mkdir -p "$safety_folder"
chown "$(stat -c '%u:%g' "$DATA_DIR/db")" "$safety_folder"
run_in_orchestrator backup "$DATABASE" "$safety_folder/bvp.sqlite3" ||
    die "the safety copy failed; nothing was deleted"
log "safety copy: $safety_folder/bvp.sqlite3"

# 2. The DB rows (one transaction).
run_in_orchestrator clear-event-data "$DATABASE" || die "clearing the DB failed; nothing was deleted"

# 3. The files. A folder that does not exist yet has nothing to delete.
for folder in "${JOB_FILE_FOLDERS[@]}"; do
    if [[ -d "$DATA_DIR/$folder" ]]; then
        count="$(find "$DATA_DIR/$folder" -mindepth 1 -type f | wc -l)"
        find "$DATA_DIR/$folder" -mindepth 1 -delete
        log "$folder/: $count files deleted"
    fi
done

log "done. Start the pipeline with: sudo ./up.sh"
log "then empty the PCs' copies too (README §15)."
