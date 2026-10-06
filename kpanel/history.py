"""Dashboard history: one sample every few minutes, persisted as JSON, drawn as sparklines.

The panel is otherwise stateless, so this is its only state on disk: a small
JSON file on the kpanel-state volume, so the graphs survive restarts and
redeploys. Writes are atomic (temp file + rename), so a crash mid-write can't
leave a half-written file.
"""

import html
import json
import os
import threading
import time

KEEP = 288  # one day at 5-minute samples; the dashboard shows the last few


class History:
    def __init__(self, path: str, keep: int = KEEP):
        self.path, self.keep, self.lock = path, keep, threading.Lock()
        self.samples = self._load()
        self.save_failed = False  # report a failing save once, not every sample

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return [s for s in data if isinstance(s, dict) and "t" in s][-self.keep:]
        except (OSError, ValueError):
            return []

    def add(self, sample: dict):
        with self.lock:
            self.samples = (self.samples + [sample])[-self.keep:]
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.samples, f)
                os.replace(tmp, self.path)
                self.save_failed = False
            except OSError as ex:
                if not self.save_failed:
                    print(f"history: could not save {self.path}: {ex} (the graphs work, but start "
                          "over on a restart; is the kpanel-state volume mounted there?)", flush=True)
                self.save_failed = True

    def series(self, key: str, n: int):
        """The last n stored (time, value) pairs that have this metric."""
        with self.lock:
            pts = [(s["t"], s[key]) for s in self.samples if s.get(key) is not None]
        return pts[-n:]


def sparkline(points, lo=None, hi=None, fmt=str, width=120, height=28):
    """Inline SVG trend line. Needs >= 2 points; '' otherwise.

    lo/hi fix the vertical scale where a natural range exists (TPS 0-20,
    players 0-max), so a 20.0 -> 19.9 wobble stays flat instead of filling the
    whole height. Every point has a hover target with its time and value; the
    last one (the live value) is highlighted.
    """
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 2:
        return ""
    vals = [v for _, v in pts]
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    pad = 4
    xs = [pad + i * (width - 2 * pad) / (len(pts) - 1) for i in range(len(pts))]

    def y(v):
        if hi == lo:
            return height / 2
        frac = (min(max(v, lo), hi) - lo) / (hi - lo)
        return height - pad - frac * (height - 2 * pad)

    line = " ".join(f"{x:.1f},{y(v):.1f}" for x, (_, v) in zip(xs, pts))
    # Only the live value gets a visible mark; past samples are the line itself,
    # each with an invisible hover target bigger than the mark.
    marks = []
    for i, (x, (t, v)) in enumerate(zip(xs, pts)):
        last = i == len(pts) - 1
        label = f'{"now" if last else time.strftime("%H:%M", time.localtime(t))}: {fmt(v)}'
        marks.append(
            f'<g><title>{html.escape(label)}</title>'
            f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="8" fill="transparent"/>'
            + (f'<circle class=now cx="{x:.1f}" cy="{y(v):.1f}" r="4"/>' if last else "") + '</g>')
    return (f'<svg class=spark width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="last {len(pts)} samples">'
            f'<polyline class=line points="{line}"/>{"".join(marks)}</svg>')
