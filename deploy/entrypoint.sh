#!/bin/sh
# PRESENCE AI — container entrypoint.
#
# The only job here is to make the data folder writable, then hand over. A
# Railway volume is created owned by root; the app runs as uid 10001 and would
# otherwise fail the first time it appends to sessions.jsonl.

set -e

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # -R because a fresh volume is empty but a reused one keeps old owners.
    chown -R 10001:10001 "$DATA_DIR" 2>/dev/null || true

    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
    fi
    echo "[entrypoint] setpriv unavailable - continuing as root" >&2
fi

exec "$@"
