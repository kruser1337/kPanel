#!/bin/bash
# Runs INSIDE the mc container, as uid 1000: what a malicious plugin could do.
#
#   isolation_poller.sh <seconds> <outdir>
#
# Polls /proc for every process in mc's PID namespace (which kpanel shares) and
# tries to read each one's environ, mem and root. Whatever it manages to read
# is saved under <outdir>; the host greps it for the test's markers afterwards,
# so this script never knows them (it would otherwise leak them itself). One
# line per attempt on stdout:
#
#   SEEN <pid> <uid> <cmdline>
#   ENV|MEM|ROOT <pid> <uid> ok|denied|none <detail>
#
# Bash builtins where it matters: the healthcheck process lives ~200 ms.
set -u
secs=$1 out=$2
mkdir -p "$out"
declare -A tried
end=$((SECONDS + secs))
self=$$

while ((SECONDS < end)); do
  for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    ((pid == self)) && continue
    uid="" ppid=""
    { while read -r k v _; do
        case $k in Uid:) uid=$v ;; PPid:) ppid=$v ;; esac
      done < "$d/status"; } 2>/dev/null || continue
    [[ -z $uid ]] && continue            # mid-exec: try again next round
    # Our own subshells and the cat/dd/ls we start: not targets.
    ((ppid == self)) && continue
    mapfile -d '' -t argv < "$d/cmdline" 2>/dev/null || continue
    cmd="${argv[*]:-}"
    [[ -z $cmd ]] && continue            # kernel thread or mid-exec
    key="$pid $cmd"
    [[ -n ${tried[$key]:-} ]] && continue
    tried[$key]=1
    echo "SEEN $pid $uid $cmd"

    # environ: what the verifier read GITHUB_TOKEN and the hash from.
    if err=$(cat "$d/environ" 2>&1 >"$out/$pid.environ"); then
      echo "ENV $pid $uid ok $(wc -c <"$out/$pid.environ") bytes"
    else
      echo "ENV $pid $uid denied ${err##*: }"
    fi

    # root: the process's view of its filesystem (kpanel's image, for kpanel).
    if err=$(ls "$d/root/" 2>&1 >/dev/null); then
      echo "ROOT $pid $uid ok"
    else
      echo "ROOT $pid $uid denied ${err##*: }"
    fi

    # mem: every writable anonymous region (heap, arenas), where hashpw.py
    # holds the typed password. The JVM's heap is gigabytes: one page of it
    # proves the read works, which is all the control needs.
    n=0 res=none detail=""
    while read -r range perms _ _ _ path; do
      [[ $perms == rw* ]] || continue
      [[ -z ${path:-} || $path == "[heap]" || $path == "[stack]" ]] || continue
      s=$((16#${range%-*})) e=$((16#${range#*-}))
      pages=$(((e - s) / 4096))
      [[ ${argv[0]##*/} == java ]] && pages=1
      ((pages > 16384)) && pages=16384  # 64 MiB per region at most
      dd if="$d/mem" bs=4096 skip=$((s / 4096)) count=$pages >>"$out/$pid.mem" 2>"$out/.dderr"
      if [[ -s $out/$pid.mem ]]; then
        res=ok
      elif [[ $res != ok ]]; then
        err=$(head -n1 "$out/.dderr") res=denied detail=${err##*: }
      fi
      ((++n >= 64)) && break
    done < <(cat "$d/maps" 2>/dev/null)
    [[ $res == ok ]] && detail="$(wc -c <"$out/$pid.mem") bytes"
    # maps itself unreadable: the same ptrace check refused us
    if ((n == 0)) && ! cat "$d/maps" >/dev/null 2>&1; then
      res=denied detail="maps: Permission denied"
    fi
    echo "MEM $pid $uid $res $detail"
  done
done
echo "DONE"
