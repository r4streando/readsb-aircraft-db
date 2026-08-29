#!/bin/bash
set -Eeuo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }

install -d -m 0755 /usr/local/lib/readsb-aircraft-db /usr/local/sbin /var/lib/readsb-aircraft-db
install -m 0755 "$SRC_DIR/readsb-db-merge.py" /usr/local/lib/readsb-aircraft-db/readsb-db-merge.py
install -m 0755 "$SRC_DIR/update-readsb-aircraft-db" /usr/local/sbin/update-readsb-aircraft-db
install -m 0644 "$SRC_DIR/readsb-aircraft-db.cron" /etc/cron.d/readsb-aircraft-db

echo "Installed updater and cron job."
echo "Run the first merge now with:"
echo "  /usr/local/sbin/update-readsb-aircraft-db"
echo
echo "Then point readsb at:"
echo "  --db-file /var/lib/readsb-aircraft-db/aircraft.csv.gz --db-file-lt"
echo "readsb monitors the DB file for changes; no cron-triggered restart is required."
