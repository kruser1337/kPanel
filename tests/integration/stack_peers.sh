#!/usr/bin/env bash
# N-05: can the other containers of the stack use the panel or the file
# manager directly, past the published port and the Host check?
#
#   tests/integration/stack_peers.sh
#
# Part 1, the base file (no login): from this machine the panel answers; from
# mc (as uid 10000, a plugin's view) and from mc-backup, http://kpanel:8080 with
# a forged Host and Sec-Fetch-Site is refused, an op over it changes nothing,
# and http://filebrowser:80 isn't reachable at all.
# Part 2, the tailscale.yml rule (KPANEL_ONLY_PEERS=tailscale) with a stand-in
# "tailscale" container (no tailnet needed): it is answered, mc is not, and
# neither is the published port.
#
# Publishes the panel on 127.0.0.1:${PORT:-18181} only.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
P=${PROJECT:-kppeer}
PORT=${PORT:-18181}
work=$(mktemp -d)
cat >"$work/test.yml" <<EOF
services:
  mc:
    ports: !reset []
  kpanel:
    ports: !override
      - "127.0.0.1:$PORT:8080"
    environment:
      KPANEL_UPDATE_CHECK: "0"
EOF
cat >"$work/ts.yml" <<'EOF'
services:
  kpanel:
    environment:
      KPANEL_ONLY_PEERS: "tailscale"
  tailscale:   # stand-in for the sidecar: same service name, same network
    image: alpine:3
    command: ["sleep", "infinity"]
EOF
# EXTRA_OVERLAY: more compose files, colon-separated, added after the test's
# own (e.g. one that undoes a fix, to see the test fail).
extra=()
IFS=: read -r -a _xo <<<"${EXTRA_OVERLAY:-}"
for _f in ${_xo[@]+"${_xo[@]}"}; do [[ -n $_f ]] && extra+=(-f "$_f"); done
DC=("$DOCKER" compose -p "$P" -f "$root/docker-compose.yml" -f "$work/test.yml" ${extra[@]+"${extra[@]}"})
cleanup() {
  "${DC[@]}" -f "$work/ts.yml" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

fail=0
check() { if [[ $1 == 1 ]]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi; }
from_host() { curl -s -o /dev/null -w '%{http_code}' -H "Host: localhost:$PORT" "http://127.0.0.1:$PORT$1" || true; }
from() {  # from <service> <user> <curl args...>: the HTTP status, or 000 if unreachable
  local svc=$1 user=$2; shift 2
  "${DC[@]}" ${EXTRA[@]+"${EXTRA[@]}"} exec -T -u "$user" "$svc" curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" 2>/dev/null | tr -d '\r' || true
}
wait_panel() {
  for _ in $(seq 60); do [[ $(from_host /) == 200 || $(from_host /) == 403 ]] && return 0; sleep 2; done
  return 1
}
FORGED=(-H "Host: localhost" -H "Sec-Fetch-Site: same-origin")

echo "== part 1: base file, no login"
EXTRA=()
"${DC[@]}" up -d --build >/dev/null 2>&1
wait_panel || { echo "FAIL: the panel never answered" >&2; exit 1; }
# ops.json exists once the server has started (it needs to download Paper first)
for _ in $(seq 100); do "${DC[@]}" exec -T mc test -f /data/ops.json 2>/dev/null && break; sleep 3; done
"${DC[@]}" exec -T mc test -f /data/ops.json 2>/dev/null ||
  { echo "FAIL: the server never started (no ops.json); see: ${DC[*]} logs mc" >&2; exit 1; }
ops_before=$("${DC[@]}" exec -T mc cat /data/ops.json 2>/dev/null | tr -d '\r' || true)
check "$([[ $(from_host /) == 200 ]] && echo 1 || echo 0)" "this machine, through the published port: 200"
s=$(from mc 10000:10000 https://api.papermc.io/)
check "$([[ $s != 000 ]] && echo 1 || echo 0)" "mc still reaches the internet (Paper, Mojang): $s"
s=$(from mc 10000:10000 "${FORGED[@]}" http://kpanel:8080/)
check "$([[ $s == 403 ]] && echo 1 || echo 0)" "mc (uid 10000) -> kpanel:8080 with a forged Host: $s"
s=$(from mc 10000:10000 "${FORGED[@]}" --data 'action=op&name=Attacker2' http://kpanel:8080/players)
check "$([[ $s == 403 ]] && echo 1 || echo 0)" "mc -> POST /players op Attacker2: $s"
sleep 2
ops_after=$("${DC[@]}" exec -T mc cat /data/ops.json 2>/dev/null | tr -d '\r' || true)
check "$([[ $ops_before == "$ops_after" && $ops_after != *Attacker2* ]] && echo 1 || echo 0)" "ops.json unchanged"
s=$(from mc-backup 0 "${FORGED[@]}" http://kpanel:8080/)
check "$([[ $s == 403 ]] && echo 1 || echo 0)" "mc-backup -> kpanel:8080: $s"
s=$(from mc 10000:10000 http://filebrowser:80/files/)
check "$([[ $s == 000 ]] && echo 1 || echo 0)" "mc -> filebrowser:80 is unreachable: ${s:-000}"
s=$(from_host /files/)
check "$([[ $s == 200 ]] && echo 1 || echo 0)" "the file manager through the panel still works: $s"

echo "== part 2: the tailscale.yml rule, with a stand-in sidecar"
EXTRA=(-f "$work/ts.yml")
"${DC[@]}" ${EXTRA[@]+"${EXTRA[@]}"} up -d >/dev/null 2>&1
wait_panel || { echo "FAIL: the panel never answered" >&2; exit 1; }
"${DC[@]}" ${EXTRA[@]+"${EXTRA[@]}"} exec -T tailscale apk add -q curl >/dev/null 2>&1 || true
s=$(from tailscale 0 "${FORGED[@]}" http://kpanel:8080/)
check "$([[ $s == 200 ]] && echo 1 || echo 0)" "the sidecar -> kpanel:8080: $s"
s=$(from mc 10000:10000 "${FORGED[@]}" http://kpanel:8080/)
check "$([[ $s == 403 ]] && echo 1 || echo 0)" "mc -> kpanel:8080: $s"
s=$(from_host /)
check "$([[ $s == 403 ]] && echo 1 || echo 0)" "the published port (there is none in tailscale.yml): $s"
exit $fail
