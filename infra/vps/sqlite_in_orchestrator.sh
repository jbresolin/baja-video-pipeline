#!/usr/bin/env bash
# Run sqlite_backup.py (or clear_event_data.py) with the orchestrator's own
# Python and SQLite.
#
#   sqlite_in_orchestrator.sh backup  /srv/pipeline/db/bvp.sqlite3 /some/folder/bvp.sqlite3
#   sqlite_in_orchestrator.sh restore /var/backups/baja/<stamp>/bvp.sqlite3 /srv/pipeline/db/bvp.sqlite3
#   sqlite_in_orchestrator.sh clear-event-data /srv/pipeline/db/bvp.sqlite3
#
# Why not the VPS's python3: a backup must be read and judged by the SQLite
# that writes the DB. On 2026-11-07 Ubuntu 24.04's SQLite 3.45.1 reported
# "NULL value in jobs.crop_position" on a DB that the orchestrator's 3.46.1
# calls intact, so every nightly backup failed. The orchestrator image is on
# the VPS anyway; this runs the script in a throwaway container from it.
#
# Which image: the existing orchestrator container's exact image when there
# is one (running or stopped), else ORCHESTRATOR_IMAGE from .env (pulled if
# missing), e.g. when restoring before the stack was ever started.
#
# The source's and destination's folders are mounted at the same paths, as
# the orchestrator's user (uid 10001, design §17), with no network.
# Exit status and output are the Python script's.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_PROJECT=baja-pipeline
DATA_UID="${BAJA_BACKUP_UID:-10001}"

usage() {
    echo "usage: $0 backup|restore <source> <destination>" >&2
    echo "       $0 clear-event-data <database>" >&2
    exit 2
}
action="${1:-}"
case "$action" in
    backup | restore)
        [[ $# -eq 3 ]] || usage
        script=sqlite_backup.py
        source_file="$(realpath -m -- "$2")"
        destination_file="$(realpath -m -- "$3")"
        script_arguments=("$action" "$source_file" "$destination_file")
        ;;
    clear-event-data)
        [[ $# -eq 2 ]] || usage
        script=clear_event_data.py
        source_file="$(realpath -m -- "$2")"
        destination_file="$source_file"
        script_arguments=("$source_file")
        ;;
    *) usage ;;
esac

orchestrator_image() {
    local container_id
    container_id="$(docker ps -aq \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
        --filter "label=com.docker.compose.service=orchestrator" | head -n 1)"
    if [[ -n "$container_id" ]]; then
        # The image ID the container was made from, not a tag that may
        # have moved since.
        docker inspect --format '{{.Image}}' "$container_id"
        return
    fi
    local env_file="$SCRIPT_DIR/.env" image=""
    if [[ -f "$env_file" ]]; then
        image="$(grep -E '^ORCHESTRATOR_IMAGE=' "$env_file" | tail -n 1 | cut -d= -f2-)"
    fi
    if [[ -z "$image" ]]; then
        echo "sqlite_in_orchestrator: no orchestrator container and no ORCHESTRATOR_IMAGE in $env_file" >&2
        return 1
    fi
    docker image inspect "$image" >/dev/null 2>&1 || docker pull --quiet "$image" >/dev/null
    echo "$image"
}

image="$(orchestrator_image)"
source_folder="$(dirname -- "$source_file")"
destination_folder="$(dirname -- "$destination_file")"

mounts=(--volume "$source_folder:$source_folder")
if [[ "$destination_folder" != "$source_folder" ]]; then
    mounts+=(--volume "$destination_folder:$destination_folder")
fi

exec docker run --rm --network none \
    --user "$DATA_UID:$DATA_UID" \
    "${mounts[@]}" \
    --volume "$SCRIPT_DIR/$script:/opt/$script:ro" \
    --entrypoint python \
    "$image" "/opt/$script" "${script_arguments[@]}"
