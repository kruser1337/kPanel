"""CPU and memory of the Minecraft JVM, read from /proc.

kpanel shares the mc container's PID namespace (compose: pid "service:mc"),
so the server's java process is visible here like a local one. That needs no
Docker socket: a socket, even behind a "read-only" proxy, would expose every
container's environment, other apps' secrets included.

CPU is a rate, so it's the change in the process's CPU time between two
readings: the average since the previous reading.
"""

import os
import threading
import time

PROC = "/proc"
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
NCPU = os.cpu_count() or 1


def find_java(proc=PROC):
    """PID of the server's JVM, or None if it isn't visible (yet)."""
    try:
        entries = os.listdir(proc)
    except OSError:
        return None
    for d in entries:
        if d.isdigit():
            try:
                with open(f"{proc}/{d}/comm") as f:
                    if f.read().strip() == "java":
                        return int(d)
            except OSError:
                continue
    return None


def cpu_ticks(pid, proc=PROC):
    """utime + stime in clock ticks. The comm field can contain spaces, so split after ')'."""
    with open(f"{proc}/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # fields 14 and 15 of stat(5)


def rss_bytes(pid, proc=PROC):
    with open(f"{proc}/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None


def meminfo(proc=PROC):
    """Host memory (meminfo isn't namespaced): {'total': bytes, 'available': bytes}."""
    out = {}
    try:
        with open(f"{proc}/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    out["total" if k == "MemTotal" else "available"] = int(rest.split()[0]) * 1024
    except OSError:
        pass
    return out


class CpuMeter:
    """CPU % of the JVM since the previous reading, as a share of ALL host cores.

    Shared by the dashboard (every page load) and the sampler (every 5 min).
    Without a recent earlier reading (first load, or the JVM restarted) it
    measures over a short window instead.
    """

    def __init__(self, proc=PROC, ncpu=NCPU, clk=CLK_TCK, window=0.5):
        self.proc, self.ncpu, self.clk, self.window = proc, ncpu, clk, window
        self.last = None  # (monotonic time, pid, ticks)
        self.lock = threading.Lock()

    def percent(self):
        with self.lock:
            pid = find_java(self.proc)
            if pid is None:
                self.last = None
                return None
            try:
                now, ticks = time.monotonic(), cpu_ticks(pid, self.proc)
                if not self.last or self.last[1] != pid or now - self.last[0] < 1:
                    time.sleep(self.window)
                    self.last = (now, pid, ticks)
                    now, ticks = time.monotonic(), cpu_ticks(pid, self.proc)
                t0, _, k0 = self.last
                self.last = (now, pid, ticks)
                dt = now - t0
                return max(0.0, (ticks - k0) / self.clk / dt / self.ncpu * 100) if dt > 0 else None
            except (OSError, ValueError, IndexError):
                self.last = None
                return None


def memory(proc=PROC):
    """{'rss': JVM resident bytes or None, 'total': host bytes, 'available': host bytes}."""
    pid = find_java(proc)
    m = meminfo(proc)
    try:
        m["rss"] = rss_bytes(pid, proc) if pid else None
    except OSError:
        m["rss"] = None
    return m
