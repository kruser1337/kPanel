#!/usr/bin/env bash
# Do backups still work as the server's service account, on an upgraded volume?
#
#   tests/integration/backup_identity.sh
#
# Starts 0.3.0's mc-backup, which hands /backups to uid 1000, and leaves a
# root-owned archive dated 30 days back in it (0.2 took backups as root). Then
# runs the current stack with a once-a-minute schedule and checks: volume-init
# handed /backups and the old archive to 10000:10000; crond holds only SETUID
# and SETGID; the backup itself runs as uid 10000 with no capability; the
# scheduled backup and `backup now` both produce archives owned by 10000; and
# the old archive is pruned (older than PRUNE_BACKUPS_DAYS).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
P=${PROJECT:-kpbak}
OLD_REF=${OLD_REF:-v0.3.0}    # /backups is 1000's
work=$(mktemp -d)
mkdir "$work/old"
git -C "$root" archive "$OLD_REF" | tar -x -C "$work/old"
cat >"$work/test.yml" <<'EOF'
services:
  mc:
    ports: !reset []
  kpanel:
    ports: !reset []
    environment:
      KPANEL_UPDATE_CHECK: "0"
  mc-backup:
    environment:
      CRON_SCHEDULE: "* * * * *"
EOF
# EXTRA_OVERLAY: more compose files, colon-separated, added after the test's
# own (e.g. one that undoes a fix, to see the test fail).
extra=()
IFS=: read -r -a _xo <<<"${EXTRA_OVERLAY:-}"
for _f in ${_xo[@]+"${_xo[@]}"}; do [[ -n $_f ]] && extra+=(-f "$_f"); done
OLD=("$DOCKER" compose -p "$P" -f "$work/old/docker-compose.yml" -f "$work/test.yml" ${extra[@]+"${extra[@]}"})
NEW=("$DOCKER" compose -p "$P" -f "$root/docker-compose.yml" -f "$work/test.yml" ${extra[@]+"${extra[@]}"})
cleanup() {
  "${NEW[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail=0
check() { if [[ $1 == 1 ]]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi; }
inb() { { "${NEW[@]}" exec -T mc-backup sh -c "$1" 2>/dev/null || true; } | tr -d '\r'; }

echo "== 0.3.0's mc-backup: /backups is 1000's, with a root-owned 30-day-old archive"
"${OLD[@]}" up -d mc-backup >/dev/null 2>&1
# Planted from a plain root container: 0.3.0's crond has no DAC_OVERRIDE, so
# even as root it can't write a /backups that is 1000's.
"$DOCKER" run --rm -v "${P}_mc-backups:/backups" alpine:3 \
  sh -c 'touch -d "@$(( $(date +%s) - 30*86400 ))" /backups/world-20250101-000000.tar.gz'
old_owner=$("${OLD[@]}" exec -T mc-backup stat -c '%u:%g' /backups /backups/world-20250101-000000.tar.gz | tr -d '\r' | tr '\n' ' ')
"${OLD[@]}" down >/dev/null 2>&1   # volumes kept

echo "== the current stack on the same volumes"
"${NEW[@]}" up -d --build >/dev/null 2>&1
# volume-init has finished before mc-backup starts; read this before a backup prunes it
old_mid=$(inb 'stat -c %u:%g /backups/world-20250101-000000.tar.gz 2>/dev/null')
for _ in $(seq 100); do "${NEW[@]}" exec -T mc test -f /data/ops.json 2>/dev/null && break; sleep 3; done
"${NEW[@]}" exec -T mc test -f /data/ops.json 2>/dev/null ||
  { echo "FAIL: the server never started; its log ends:" >&2; "${NEW[@]}" logs --tail 15 mc >&2; exit 1; }

dir_owner=$(inb 'stat -c %u:%g /backups')
crond=$(inb 'for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = crond ] && grep -E "^(Uid|CapEff|CapPrm|CapBnd):" $p/status; done' | awk '{print $1, $2}' | tr '\n' ' ')

echo "== waiting for the scheduled backup (once a minute)"
for _ in $(seq 50); do
  n=$(inb 'ls /backups/world-2026*.tar.gz 2>/dev/null | wc -l')
  [[ ${n:-0} -ge 1 ]] && break
  sleep 3
done
sched=$(inb 'stat -c "%u:%g %n" /backups/world-2026*.tar.gz 2>/dev/null | head -n1')

echo "== backup now, watching the processes it starts"
"${NEW[@]}" exec -T mc-backup sh -c '
  backup now >/tmp/now.log 2>&1 & bp=$!
  while kill -0 $bp 2>/dev/null; do
    for p in /proc/[0-9]*; do
      # one read of status: a process that exits in between must not show up half-read
      st=$(cat $p/status 2>/dev/null) || continue
      u=$(echo "$st" | awk "/^Uid:/{print \$2}"); c=$(echo "$st" | awk "/^CapEff:/{print \$2}")
      [ "$u" = 10000 ] && [ -n "$c" ] && echo "JOB $u $c $(tr "\0" " " < $p/cmdline 2>/dev/null | cut -c1-60)"
    done
  done | sort -u >/tmp/jobs.txt
  wait $bp' >/dev/null 2>&1 || true
jobs=$(inb 'cat /tmp/jobs.txt')
newest=$(inb 'ls -t /backups/world-2026*.tar.gz | head -n1 | xargs stat -c "%u:%g %s"')
old_left=$(inb 'ls /backups/world-20250101-000000.tar.gz 2>/dev/null | wc -l')

echo
echo "   before: /backups and old archive owned $old_owner"
echo "   crond:  $crond"
echo "   jobs seen as uid 10000 during 'backup now':"
sed 's/^/     /' <<<"$jobs"
check "$([[ $old_owner == "1000:1000 0:0 " ]] && echo 1 || echo 0)" "control: 0.3.0 left /backups 1000's and the old archive root's"
check "$([[ $dir_owner == 10000:10000 ]] && echo 1 || echo 0)" "/backups handed to 10000:10000 ($dir_owner)"
check "$([[ $old_mid == 10000:10000 ]] && echo 1 || echo 0)" "so was the old archive, before the pruning ($old_mid)"
check "$([[ $crond == *"Uid: 0"* && $crond == *"CapEff: 00000000000000c0"* && $crond == *"CapBnd: 00000000000000c0"* ]] && echo 1 || echo 0)" "crond: root with SETUID, SETGID only (c0)"
check "$([[ $sched == 10000:10000* ]] && echo 1 || echo 0)" "the scheduled backup is owned by 10000: ${sched:-none}"
check "$([[ $newest == 10000:10000* && ${newest##* } -gt 1000 ]] && echo 1 || echo 0)" "backup now: owned by 10000, non-empty: ${newest:-none}"
check "$([[ -n $jobs ]] && ! grep -qv ' 0000000000000000 ' <<<"$jobs" && echo 1 || echo 0)" "every uid-10000 backup process had no capability"
check "$([[ $old_left == 0 ]] && echo 1 || echo 0)" "the old archive was pruned"
exit $fail
