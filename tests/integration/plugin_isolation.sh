#!/usr/bin/env bash
# N-01: can code in the Minecraft server read the panel's secrets?
#
#   tests/integration/plugin_isolation.sh
#
# Brings up the real stack (project "kpiso", nothing published) with marker
# secrets: a GitHub token, a panel password hashed by the hashpw service, and a
# second password typed into hashpw.py while the test watches. Then, as uid
# 1000 inside mc (a plugin's uid and namespace), it polls /proc and tries to
# read environ, mem and root of every process it can see:
#
#   A. while the kpanel healthcheck fires and while
#      `docker compose exec kpanel python hashpw.py` (the 0.3.0 instruction)
#      waits at its second prompt;
#   B. while `docker compose run --rm hashpw` (the documented way since 0.3.1)
#      waits at its second prompt.
#
# Passes only if no marker is found anywhere, every kpanel process refused all
# three reads, and the controls held: the poller did see the healthcheck, the
# panel and the exec'd hashpw.py (else it proved nothing), it could read mc's
# own processes (so the method works), and B's hashpw.py was not visible.
#
# Needs: docker (or DOCKER=podman) with compose >= 2.24, python3, network for
# the first pull and the Paper download. Takes about 3 minutes plus the pull.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
P=${PROJECT:-kpiso}
WATCH_A=${WATCH_A:-75}   # > 2 healthcheck intervals (30 s)
WATCH_B=${WATCH_B:-30}
work=$(mktemp -d)
tag=$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 12 || true)
TOKEN="github_pat_KPISOTOKEN_$tag"
PW="KPISOPW_${tag}_hashed"    # becomes KPANEL_PASSWORD_HASH
PW2="KPISOPW_${tag}_typed"    # typed into hashpw.py during the watch
DC=("$DOCKER" compose -p "$P" -f "$root/docker-compose.yml" -f "$here/isolation.yml")
# EXTRA_OVERLAY: one more compose file, e.g. one that puts kpanel back on uid
# 1000 to see this test fail the way 0.3.0 did.
[[ ${EXTRA_OVERLAY:-} ]] && DC+=(-f "$EXTRA_OVERLAY")
DC+=(--env-file "$work/env")

cleanup() {
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
quiet() { grep -v -e 'external compose provider' -e '^.\[0m$' || true; }

: >"$work/env"
echo "== making the panel login with the hashpw service"
line=$(printf %s "$PW" | "${DC[@]}" run --rm -T --build hashpw 2>"$work/hashpw.err" | grep '^KPANEL_PASSWORD_HASH=' || true)
if [[ -z $line ]]; then
  cat "$work/hashpw.err" >&2
  echo "FAIL: hashpw printed no KPANEL_PASSWORD_HASH line" >&2
  exit 1
fi
HASH=${line#KPANEL_PASSWORD_HASH=\'}
HASH=${HASH%\'}
printf '%s\nKPANEL_GITHUB_TOKEN=%s\n' "$line" "$TOKEN" >"$work/env"

echo "== starting the stack (project $P)"
"${DC[@]}" up -d --build 2>&1 | quiet | tail -n 3
for _ in $(seq 60); do
  "${DC[@]}" exec -T kpanel true >/dev/null 2>&1 && break
  sleep 2
done
"${DC[@]}" exec -T kpanel true >/dev/null 2>&1 || { echo "FAIL: kpanel never came up" >&2; "${DC[@]}" logs kpanel | tail -20; exit 1; }
yama=$("${DC[@]}" exec -T mc cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null | tr -d '\r' || echo "n/a")
kuid=$("${DC[@]}" exec -T kpanel id 2>/dev/null | tr -d '\r')
echo "   kpanel runs as: $kuid; kernel.yama.ptrace_scope=$yama"

poll() {  # poll <seconds> <outdir> > log
  "${DC[@]}" exec -T -u 1000:1000 mc bash -s -- "$1" "$2" <"$here/isolation_poller.sh"
}

echo "== A: watching for ${WATCH_A}s: healthchecks + 'exec kpanel python hashpw.py' at its prompt"
poll "$WATCH_A" /tmp/kpiso-a >"$work/a.log" 2>&1 &
pa=$!
sleep 5
python3 "$here/pty_type.py" "$PW2" 40 "${DC[@]}" exec kpanel python hashpw.py >"$work/pty-a.log" 2>&1 || true
wait $pa || true

echo "== B: watching for ${WATCH_B}s: 'run --rm hashpw' at its prompt"
poll "$WATCH_B" /tmp/kpiso-b >"$work/b.log" 2>&1 &
pb=$!
sleep 3
python3 "$here/pty_type.py" "$PW2" 20 "${DC[@]}" run --rm hashpw >"$work/pty-b.log" 2>&1 || true
wait $pb || true

# --- verdict -------------------------------------------------------------
fail=0
check() {  # check <ok?> <description>
  if [[ $1 == 1 ]]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi
}
count() { grep -c -E "$1" "$2" || true; }
is() { [[ $1 -gt 0 ]] && echo 1 || echo 0; }

# Markers in everything the poller saved (grepped inside mc, patterns on stdin,
# so they are never on a command line) and in the logs it printed.
hits=$(printf '%s\n%s\n%s\n%s\n' "$TOKEN" "$PW" "$PW2" "$HASH" |
  "${DC[@]}" exec -T -u 1000:1000 mc sh -c 'grep -l -a -r -F -f - /tmp/kpiso-a /tmp/kpiso-b' 2>/dev/null | tr -d '\r' || true)
loghits=$(cat "$work/a.log" "$work/b.log" | grep -c -F -e "$TOKEN" -e "$PW" -e "$PW2" -e "$HASH" || true)
saved=$("${DC[@]}" exec -T mc sh -c 'cat /tmp/kpiso-a/* /tmp/kpiso-b/* 2>/dev/null | wc -c' | tr -d '\r ')

# A "kpanel process": not the server's uid, or the panel's own commands.
KP='(app\.py|hashpw|healthz)'
kpanel_pids() { awk -v re="$KP" '$1=="SEEN" && ($3!="1000" || $0 ~ re) {print $2}' "$1" | sort -u; }
leaks=0
for log in "$work/a.log" "$work/b.log"; do
  for pid in $(kpanel_pids "$log"); do
    n=$(grep -E "^(ENV|MEM|ROOT) $pid .* ok" "$log" || true)
    if [[ -n $n ]]; then echo "  readable kpanel process: $n"; leaks=1; fi
  done
done

echo
echo "== result (ptrace_scope=$yama, $(grep -c '^SEEN' "$work/a.log" || true)+$(grep -c '^SEEN' "$work/b.log" || true) processes seen, $saved bytes read)"
check "$([[ -z $hits && $loghits == 0 ]] && echo 1 || echo 0)" "no marker (token, hash, either password) in anything read: ${hits:-none}"
check "$((1 - leaks))" "every kpanel process refused environ, mem and root"
check "$(is "$(grep -E '^SEEN .*healthz' "$work/a.log" | grep -vc ' 1000 ' || true)")" "saw the healthcheck (control), and it did not run as the server's uid"
check "$(is "$(count '^SEEN .*app\.py' "$work/a.log")")" "control: saw the panel's main process"
check "$(is "$(count '^SEEN .*hashpw\.py' "$work/a.log")")" "control: saw the exec'd hashpw.py"
check "$(grep -q '^typed the password' "$work/pty-a.log" && echo 1 || echo 0)" "control: the password was typed into the exec'd hashpw.py"
check "$(grep -q '^typed the password' "$work/pty-b.log" && echo 1 || echo 0)" "control: the password was typed into 'run hashpw'"
check "$([[ $(count '^SEEN .*hashpw\.py' "$work/b.log") == 0 ]] && echo 1 || echo 0)" "'run hashpw' is not in the server's process namespace"
check "$(is "$(count '^ENV [0-9]+ 1000 ok' "$work/a.log")")" "control: the server's own processes' environ is readable (the method works)"
if [[ $yama == 0 ]]; then
  check "$(is "$(count '^MEM [0-9]+ 1000 ok' "$work/a.log")")" "control: the server's own processes' memory is readable (Yama 0)"
else
  echo "  note  ptrace_scope=$yama: mem of same-uid processes is refused by Yama anyway; the uid check is what this run shows"
fi

echo
echo "-- kpanel processes seen (pid uid cmdline) and what each read returned:"
for log in "$work/a.log" "$work/b.log"; do
  for pid in $(kpanel_pids "$log"); do
    grep -E "^SEEN $pid " "$log" | head -n1 | cut -c6-
    grep -E "^(ENV|MEM|ROOT) $pid " "$log" | sort -u | sed 's/^/      /'
  done
done
if [[ ${KEEP_LOGS:-} ]]; then cp "$work"/*.log "$KEEP_LOGS"/; echo "logs copied to $KEEP_LOGS"; fi
exit $fail
