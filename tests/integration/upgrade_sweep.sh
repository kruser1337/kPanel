#!/usr/bin/env bash
# An in-place upgrade from an older kPanel, following CHANGELOG's Upgrading
# steps for compose/lan.yml, then a sweep for every password it ever held.
#
#   tests/integration/upgrade_sweep.sh [old-ref]   (default 5a8e779, 0.2.2;
#                                                   2b032d8 is 0.3.0 as verified)
#
# The old install gets marker secrets: KPANEL_BASIC_AUTH=admin:<panel marker>,
# FILES_PASSWORD=<files marker>, a GitHub token marker. After the upgrade:
# the plaintext login still works (deprecated), the world, an op and a MOTD
# edit survive; then hashpw makes a hash of a third marker password, .env is
# swapped as documented, and only that password logs in. Finally every volume
# of the project (the orphaned kpanel-data included), every container's
# environment and every container's log is grepped for the markers:
#
#   panel password, files password, new password: 0 hits anywhere
#   GitHub token: only in the kpanel container's environment (by design)
#
# Publishes the panel on 127.0.0.1:${PORT:-18282} only.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
OLD_REF=${1:-5a8e779}
P=${PROJECT:-kpup$(echo "$OLD_REF" | cut -c1-4)}
PORT=${PORT:-18282}
work=$(mktemp -d)
mkdir "$work/old"
git -C "$root" archive "$OLD_REF" | tar -x -C "$work/old"
tag=$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 10 || true)
PANEL="KPUPpanel_${tag}_x"
FILES="KPUPfiles_${tag}_x"
NEWPW="KPUPnewpw_${tag}_x"
TOKEN="github_pat_KPUPtoken_${tag}"
cat >"$work/test.yml" <<EOF
services:
  mc:
    ports: !reset []
  filebrowser:
    ports: !reset []
  kpanel:
    ports: !override
      - "127.0.0.1:$PORT:8080"
    environment:
      KPANEL_UPDATE_CHECK: "0"
EOF
printf 'KPANEL_BASIC_AUTH=admin:%s\nFILES_PASSWORD=%s\nKPANEL_GITHUB_TOKEN=%s\n' "$PANEL" "$FILES" "$TOKEN" >"$work/.env"
# EXTRA_OVERLAY: more compose files, colon-separated, added after the test's
# own (e.g. one that undoes a fix, to see the test fail).
extra=()
IFS=: read -r -a _xo <<<"${EXTRA_OVERLAY:-}"
for _f in ${_xo[@]+"${_xo[@]}"}; do [[ -n $_f ]] && extra+=(-f "$_f"); done
OLD=("$DOCKER" compose -p "$P" --project-directory "$work/old" -f "$work/old/docker-compose.yml" -f "$work/old/compose/lan.yml" -f "$work/test.yml" ${extra[@]+"${extra[@]}"} --env-file "$work/.env")
NEW=("$DOCKER" compose -p "$P" --project-directory "$root" -f "$root/docker-compose.yml" -f "$root/compose/lan.yml" -f "$work/test.yml" ${extra[@]+"${extra[@]}"} --env-file "$work/.env")
cleanup() {
  "${NEW[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  "$DOCKER" volume rm "${P}_kpanel-data" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail=0
check() { if [[ $1 == 1 ]]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi; }
login() { curl -s -o /dev/null -w '%{http_code}' -u "admin:$1" -H 'Host: localhost' "http://127.0.0.1:$PORT/logs" || true; }
wait_started() {  # wait_started <compose...>: the server is up once RCON answers
  for _ in $(seq 120); do "$@" exec -T mc rcon-cli list >/dev/null 2>&1 && return 0; sleep 3; done
  return 1
}
wait_panel() { for _ in $(seq 60); do [[ $(login x) != 000 ]] && return 0; sleep 2; done; return 1; }

echo "== $OLD_REF with compose/lan.yml and marker secrets"
"${OLD[@]}" up -d --build >/dev/null 2>&1
wait_started "${OLD[@]}" || { echo "FAIL: the old server never started; its log ends:" >&2; "${OLD[@]}" logs --tail 15 mc >&2; exit 1; }
wait_panel
old_login=$(login "$PANEL")
"${OLD[@]}" exec -T mc rcon-cli op Notch >/dev/null 2>&1 || true
save=$(curl -s -o /dev/null -w '%{http_code}' -u "admin:$PANEL" -H 'Host: localhost' -H 'Sec-Fetch-Site: same-origin' \
  --data-urlencode 'p:motd=kept across the upgrade' "http://127.0.0.1:$PORT/save" || true)
echo "   old: login $old_login, MOTD save $save"
"${OLD[@]}" down >/dev/null 2>&1   # volumes kept: an in-place upgrade
# Control for the sweep: where the old version left a password, it finds it.
before=$("$DOCKER" run --rm -v "${P}_filebrowser-db:/v:ro" alpine:3 sh -c "grep -r -l -a -F '$FILES' /v || true" | wc -l | tr -d ' ')

echo "== upgrade: git pull && docker compose up -d --build (same .env)"
"${NEW[@]}" up -d --build >/dev/null 2>&1
wait_started "${NEW[@]}" || { echo "FAIL: the server never started after the upgrade; its log ends:" >&2; "${NEW[@]}" logs --tail 15 mc >&2; exit 1; }
wait_panel
plain_login=$(login "$PANEL")
banner=$(curl -s -u "admin:$PANEL" -H 'Host: localhost' "http://127.0.0.1:$PORT/" | grep -c 'run --rm --build hashpw' || true)
ops=$("${NEW[@]}" exec -T mc cat /data/ops.json | tr -d '\r')
motd=$("${NEW[@]}" exec -T mc grep '^motd=' /data/server.properties | tr -d '\r')

echo "== swap the plaintext login for a hash, as CHANGELOG says"
line=$(printf %s "$NEWPW" | "${NEW[@]}" run --rm -T --build hashpw 2>/dev/null | grep '^KPANEL_PASSWORD_HASH=' || true)
{ echo "$line"; echo "KPANEL_GITHUB_TOKEN=$TOKEN"; } >"$work/.env"   # BASIC_AUTH replaced, FILES_PASSWORD deleted
"${NEW[@]}" up -d >/dev/null 2>&1
wait_panel
new_login=$(login "$NEWPW")
old_after=$(login "$PANEL")

echo "== sweep: volumes, container environments, logs"
vols=$("$DOCKER" volume ls -q | grep "^${P}_" | tr '\n' ' ')
pat="$work/markers"
printf '%s\n%s\n%s\n' "$PANEL" "$FILES" "$NEWPW" >"$pat"
vol_hits=""
for v in $vols; do
  h=$("$DOCKER" run --rm -v "$v:/v:ro" -v "$pat:/markers:ro" alpine:3 sh -c 'grep -r -l -a -F -f /markers /v || true' | tr '\n' ' ')
  [[ -n $h ]] && vol_hits+="$v: $h"
done
env_hits="" token_where=""
for c in $("${NEW[@]}" ps -a -q); do
  name=$("$DOCKER" inspect -f '{{.Name}}' "$c")
  envs=$("$DOCKER" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c")
  grep -q -F -f "$pat" <<<"$envs" && env_hits+="$name "
  grep -q -F "$TOKEN" <<<"$envs" && token_where+="$name "
done
log_hits=$("${NEW[@]}" logs --no-color 2>&1 | grep -c -F -f "$pat" -e "$TOKEN" || true)

echo "   volumes swept: $vols"
check "$([[ $old_login == 200 ]] && echo 1 || echo 0)" "control: the old install logged in with its plaintext password ($old_login)"
echo "  info  before the upgrade, the files password was in $before file(s) of filebrowser-db (0.2.2: 1; 0.3.0 never stored it)"
check "$([[ $plain_login == 200 && $banner -gt 0 ]] && echo 1 || echo 0)" "after the upgrade the plaintext login still works ($plain_login), with the hashpw reminder"
check "$([[ $ops == *Notch* && $motd == *"kept across the upgrade"* ]] && echo 1 || echo 0)" "the op and the MOTD edit survived"
check "$([[ -n $line ]] && echo 1 || echo 0)" "hashpw printed a KPANEL_PASSWORD_HASH line"
check "$([[ $new_login == 200 && $old_after == 401 ]] && echo 1 || echo 0)" "after the swap only the new password logs in ($new_login / old $old_after)"
check "$([[ -z $vol_hits ]] && echo 1 || echo 0)" "no password marker in any volume: ${vol_hits:-none}"
check "$([[ -z $env_hits ]] && echo 1 || echo 0)" "no password marker in any container environment: ${env_hits:-none}"
check "$([[ $log_hits == 0 ]] && echo 1 || echo 0)" "no marker (token included) in any log: $log_hits"
check "$([[ $token_where == *kpanel* && $(wc -w <<<"$token_where") -eq 1 ]] && echo 1 || echo 0)" "the GitHub token is only in the panel's environment: $token_where"
exit $fail
