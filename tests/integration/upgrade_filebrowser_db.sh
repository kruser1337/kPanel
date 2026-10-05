#!/usr/bin/env bash
# N-03: does upgrading from 0.2.2 leave the old file-manager password on disk?
#
#   tests/integration/upgrade_filebrowser_db.sh
#
# Runs the real 0.2.2 file manager with compose/lan.yml and a marker
# FILES_PASSWORD, so its database holds that password the way users' do. Then
# starts the current docker-compose.yml on the same volumes and checks that
# the marker is gone from every file in the filebrowser-db volume, that the
# file manager answers, and that its new database is a noauth one. A second
# start must keep that database (only one holding a password is dropped).
#
# Only the filebrowser service runs; nothing is published.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
P=${PROJECT:-kpfbdb}
OLD_REF=${OLD_REF:-5a8e779}   # kPanel 0.2.2
work=$(mktemp -d)
tag=$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 12 || true)
MARK="KPFBMARK_${tag}_files"
mkdir "$work/old"
git -C "$root" archive "$OLD_REF" | tar -x -C "$work/old"
cat >"$work/test.yml" <<'EOF'
services:
  filebrowser:
    ports: !reset []
    sysctls:
      net.ipv4.ip_unprivileged_port_start: "0"
EOF
OLD=("$DOCKER" compose -p "$P" -f "$work/old/docker-compose.yml" -f "$work/old/compose/lan.yml" -f "$work/test.yml")
# NEW_COMPOSE: upgrade to another compose file (e.g. 0.3.0's, to see this fail).
NEW=("$DOCKER" compose -p "$P" -f "${NEW_COMPOSE:-$root/docker-compose.yml}" -f "$work/test.yml")
cleanup() {
  "${NEW[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

db() {  # db <shell command>: run against the filebrowser-db volume, read-only
  "$DOCKER" run --rm -v "${P}_filebrowser-db:/d:ro" alpine:3 sh -c "$1"
}
wait_db() {
  for _ in $(seq 60); do
    db 'test -s /d/database.db' 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

echo "== 0.2.2 ($OLD_REF) file manager with compose/lan.yml and FILES_PASSWORD=<marker>"
export FILES_PASSWORD="$MARK" KPANEL_BASIC_AUTH="admin:unused-in-this-test"
"${OLD[@]}" up -d filebrowser >/dev/null 2>&1
wait_db || { echo "FAIL: 0.2.2 file manager wrote no database" >&2; exit 1; }
sleep 3
"${OLD[@]}" stop filebrowser >/dev/null 2>&1
before=$(db "grep -l -a -r -F '$MARK' /d || true" | wc -l | tr -d ' ')
unset FILES_PASSWORD KPANEL_BASIC_AUTH

echo "== current file manager on the same volume"
"${NEW[@]}" up -d --force-recreate filebrowser >/dev/null 2>&1
wait_db || { echo "FAIL: the current file manager wrote no database" >&2; exit 1; }
sleep 3
logs=$("${NEW[@]}" logs filebrowser 2>&1)
after=$(db "grep -l -a -r -F '$MARK' /d || true" | wc -l | tr -d ' ')
fresh=$(db "grep -a -c -e '\"noauth\":true' /d/database.db" || true)
ino1=$(db 'stat -c %i /d/database.db')
# The slim image has no HTTP client: ask from a throwaway container on the stack's network.
answers=$("$DOCKER" run --rm --network "${P}_default" alpine:3 wget -q -O /dev/null http://filebrowser:80/files/ 2>&1 && echo yes || echo no)

echo "== second start: a clean database is kept"
"${NEW[@]}" restart filebrowser >/dev/null 2>&1
sleep 5
ino2=$(db 'stat -c %i /d/database.db')
logs2=$("${NEW[@]}" logs filebrowser 2>&1 || true)  # both starts

fail=0
check() { if [[ $1 == 1 ]]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi; }
check "$([[ $before -gt 0 ]] && echo 1 || echo 0)" "control: 0.2.2 stored the password in plain text ($before file(s) in filebrowser-db)"
check "$([[ $after == 0 ]] && echo 1 || echo 0)" "after the upgrade, no file in filebrowser-db holds it ($after)"
check "$(grep -q 'removed the file manager' <<<"$logs" && echo 1 || echo 0)" "the upgrade said so in the file manager's log"
check "$([[ ${fresh:-0} -gt 0 ]] && echo 1 || echo 0)" "the new database is a noauth one"
check "$([[ $answers == yes ]] && echo 1 || echo 0)" "the file manager answers on /files/"
removals=$(grep -c 'removed the file manager' <<<"$logs2" || true)
check "$([[ $ino1 == "$ino2" && $removals == 1 ]] && echo 1 || echo 0)" "a restart keeps the clean database (inode $ino1 -> $ino2, removed $removals time(s) over two starts)"
[[ ${DEBUG:-} ]] && printf '%s\n' "--- log after restart:" "$logs2"
exit $fail
