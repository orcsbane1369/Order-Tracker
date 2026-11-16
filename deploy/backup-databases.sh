#!/bin/bash
# Nightly backup of the databases for Work Hours, Bills Tracker and Order Tracker.
#
# Copies a clean snapshot of each app's SQLite database into a folder in your backups share:
#   /mnt/user/Unraid_Backups/app-databases/<app>/<app>-YYYY-MM-DD-HHMM.db
# The snapshot is taken from inside the running container with SQLite's own backup call, so it is safe while the app is
# in use (a plain file copy of a database that is being written to can be damaged). Each snapshot is checked after it
# is taken, and snapshots older than KEEP_DAYS days are removed.
#
# Run it from Unraid's User Scripts plugin (Settings > User Scripts), custom schedule  0 3 * * *  (3 AM every night).
# Change the three settings below if your share, retention or container names differ.

SHARE="${SHARE:-/mnt/user/Unraid_Backups}"      # must already exist (this script will not create a new share). Capitals matter.
DEST="${DEST:-$SHARE/app-databases}"
KEEP_DAYS="${KEEP_DAYS:-90}"

# folder name : container name (as shown in the Docker tab) : database file inside the container's /data folder
APPS="workhours:workhours:workhours.db billstracker:bills-tracker:bills.db ordertracker:ordertracker:orders.db"

STAMP=$(date +%Y-%m-%d-%H%M)
failed=0

if [ ! -d "$SHARE" ]; then
  echo "ERROR: $SHARE does not exist. Check the share name (SHARE= at the top of this script). Nothing was backed up."
  exit 1
fi

for entry in $APPS; do
  IFS=: read -r name container dbfile <<< "$entry"
  file="$name-$STAMP.db"
  echo "== $name"

  if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]; then
    echo "   SKIPPED: container '$container' is not running (or does not exist)"
    failed=1
    continue
  fi

  mkdir -p "$DEST/$name" || { echo "   FAILED: cannot create $DEST/$name"; failed=1; continue; }

  # 1. snapshot + integrity check, written inside the container's own data folder
  if ! docker exec "$container" python -c "
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(dst), exist_ok=True)
s = sqlite3.connect(src); d = sqlite3.connect(dst); s.backup(d); s.close(); d.close()
c = sqlite3.connect(dst); ok = c.execute('PRAGMA integrity_check').fetchone()[0]; c.close()
sys.exit(0 if ok == 'ok' else 3)
" "/data/$dbfile" "/data/backups/$file"; then
    echo "   FAILED: could not snapshot /data/$dbfile (is the database file name right?)"
    docker exec "$container" rm -f "/data/backups/$file" >/dev/null 2>&1
    failed=1
    continue
  fi

  # 2. move it out to the backups share, then tidy up the copy left in the app's folder
  if docker cp "$container:/data/backups/$file" "$DEST/$name/$file" && [ -s "$DEST/$name/$file" ]; then
    docker exec "$container" rm -f "/data/backups/$file" >/dev/null 2>&1
    echo "   saved $DEST/$name/$file ($(du -h "$DEST/$name/$file" | cut -f1))"
  else
    echo "   FAILED: could not copy the snapshot to $DEST/$name"
    failed=1
    continue
  fi

  # 3. remove old snapshots
  find "$DEST/$name" -maxdepth 1 -name "$name-*.db" -mtime +"$KEEP_DAYS" -print -delete | sed 's/^/   removed old backup /'
done

if [ "$failed" -ne 0 ]; then
  echo "FINISHED WITH PROBLEMS: see the lines above."
  exit 1
fi
echo "All backups done."
