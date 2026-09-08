#!/bin/sh
set -eu

umask 077

BACKUP_DIR="${POSTGRES_BACKUP_DIR:-/app/data/backups}"
RETENTION_COUNT="${POSTGRES_BACKUP_RETENTION_COUNT:-7}"
INTERVAL_SECONDS="${POSTGRES_BACKUP_INTERVAL_SECONDS:-86400}"
INITIAL_DELAY_SECONDS="${POSTGRES_BACKUP_INITIAL_DELAY_SECONDS:-300}"
RUN_ONCE="${POSTGRES_BACKUP_RUN_ONCE:-0}"
TMP_DUMP=""
TMP_SHA=""

require_integer() {
  name="$1"
  value="$2"
  minimum="$3"
  case "$value" in
    ''|*[!0-9]*)
      echo "$name must be an integer >= $minimum" >&2
      exit 2
      ;;
  esac
  if [ "$value" -lt "$minimum" ]; then
    echo "$name must be >= $minimum" >&2
    exit 2
  fi
}

cleanup() {
  if [ -n "$TMP_DUMP" ]; then
    rm -f "$TMP_DUMP"
  fi
  if [ -n "$TMP_SHA" ]; then
    rm -f "$TMP_SHA"
  fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

prune_backups() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'stockscanner_*.dump' -print \
    | sort -r \
    | awk -v keep="$RETENTION_COUNT" 'NR > keep { print }' \
    | while IFS= read -r old_dump; do
        [ -n "$old_dump" ] || continue
        rm -f "$old_dump" "$old_dump.sha256"
      done
}

run_backup() {
  timestamp="$(date -u +%Y%m%d_%H%M%S)"
  instance_id="$(printf '%s' "${HOSTNAME:-container}" | tr -cd 'A-Za-z0-9_.-' | cut -c1-32)"
  [ -n "$instance_id" ] || instance_id="container"
  destination="$BACKUP_DIR/stockscanner_${timestamp}_${instance_id}.dump"
  TMP_DUMP="$BACKUP_DIR/.stockscanner_backup_${timestamp}_${instance_id}_$$.dump"
  TMP_SHA="$BACKUP_DIR/.stockscanner_backup_${timestamp}_${instance_id}_$$.sha256"
  rm -f "$TMP_DUMP" "$TMP_SHA"

  if ! pg_dump \
    --host=postgres \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --format=custom \
    --file="$TMP_DUMP"; then
    echo "$(date -u +%FT%TZ): PostgreSQL backup failed" >&2
    return 1
  fi

  if [ ! -s "$TMP_DUMP" ] || ! pg_restore --list "$TMP_DUMP" >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ): PostgreSQL backup validation failed" >&2
    return 1
  fi

  if ! digest="$(sha256sum "$TMP_DUMP" | awk '{print $1}')"; then
    echo "$(date -u +%FT%TZ): PostgreSQL backup checksum failed" >&2
    return 1
  fi
  if ! printf '%s  %s\n' "$digest" "$(basename "$destination")" > "$TMP_SHA"; then
    echo "$(date -u +%FT%TZ): PostgreSQL backup checksum write failed" >&2
    return 1
  fi
  if ! mv "$TMP_DUMP" "$destination"; then
    echo "$(date -u +%FT%TZ): PostgreSQL backup publish failed" >&2
    return 1
  fi
  TMP_DUMP=""
  if ! mv "$TMP_SHA" "$destination.sha256"; then
    rm -f "$destination"
    echo "$(date -u +%FT%TZ): PostgreSQL checksum publish failed" >&2
    return 1
  fi
  TMP_SHA=""

  prune_backups
  echo "$(date -u +%FT%TZ): PostgreSQL backup verified: $destination"
}

require_integer POSTGRES_BACKUP_RETENTION_COUNT "$RETENTION_COUNT" 2
require_integer POSTGRES_BACKUP_INTERVAL_SECONDS "$INTERVAL_SECONDS" 1
require_integer POSTGRES_BACKUP_INITIAL_DELAY_SECONDS "$INITIAL_DELAY_SECONDS" 0
case "$RUN_ONCE" in
  0|1) ;;
  *)
    echo "POSTGRES_BACKUP_RUN_ONCE must be 0 or 1" >&2
    exit 2
    ;;
esac

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"

mkdir -p "$BACKUP_DIR"

if [ "$RUN_ONCE" = "1" ]; then
  run_backup
  exit $?
fi

echo "Backup service started: retention=$RETENTION_COUNT interval=${INTERVAL_SECONDS}s"
sleep "$INITIAL_DELAY_SECONDS"
while true; do
  if run_backup; then
    :
  else
    echo "$(date -u +%FT%TZ): backup attempt failed; retaining prior verified dumps" >&2
  fi
  sleep "$INTERVAL_SECONDS"
done
