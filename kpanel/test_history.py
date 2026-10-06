"""Dashboard history and sparklines.

    cd kpanel && python -m unittest test_history -v
"""

import os
import re
import tempfile
import unittest

import history


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sub", "history.json")

    def test_survives_a_restart(self):
        h = history.History(self.path)
        h.add({"t": 1, "players": 0})
        h.add({"t": 2, "players": 3})
        again = history.History(self.path)  # like the panel after a redeploy
        self.assertEqual(again.series("players", 5), [(1, 0), (2, 3)])

    def test_keeps_only_the_newest(self):
        h = history.History(self.path, keep=3)
        for i in range(10):
            h.add({"t": i, "tps": 20.0})
        self.assertEqual([t for t, _ in h.series("tps", 99)], [7, 8, 9])
        self.assertEqual(len(history.History(self.path, keep=3).samples), 3)

    def test_series_skips_samples_missing_the_metric(self):
        h = history.History(self.path)
        h.add({"t": 1, "tps": 20.0})
        h.add({"t": 2})                 # e.g. RCON was down for that sample
        h.add({"t": 3, "tps": 19.5})
        self.assertEqual(h.series("tps", 5), [(1, 20.0), (3, 19.5)])

    def test_an_unwritable_volume_is_reported_once_not_every_sample(self):
        """A hand-built compose file that still mounts the old kpanel-data
        volume (owned by the old uid): CHANGELOG says the log says so once."""
        import contextlib, io
        os.makedirs(os.path.dirname(self.path))
        os.chmod(os.path.dirname(self.path), 0o500)
        self.addCleanup(os.chmod, os.path.dirname(self.path), 0o700)
        h = history.History(self.path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for i in range(5):
                h.add({"t": i, "players": i})
        self.assertEqual(out.getvalue().count("could not save"), 1)
        self.assertEqual(len(h.series("players", 9)), 5)  # the graphs still work

    def test_corrupt_file_starts_empty_instead_of_crashing(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(history.History(self.path).samples, [])

    def test_unwritable_path_does_not_raise(self):
        h = history.History("/proc/definitely/not/writable/history.json")
        h.add({"t": 1, "players": 1})  # logs, keeps the sample in memory
        self.assertEqual(h.series("players", 5), [(1, 1)])


def ys(svg):
    return [float(y) for y in re.findall(r'<circle cx="[\d.]+" cy="([\d.]+)" r="8" fill="transparent"', svg)]


class Sparkline(unittest.TestCase):
    def test_needs_two_points(self):
        self.assertEqual(history.sparkline([]), "")
        self.assertEqual(history.sparkline([(1, 5)]), "")

    def test_fixed_scale_keeps_small_wobble_flat(self):
        pts = [(1, 20.0), (2, 19.9), (3, 20.0)]
        auto = ys(history.sparkline(pts))
        fixed = ys(history.sparkline(pts, lo=0, hi=20))
        self.assertGreater(max(auto) - min(auto), 15)   # auto-scaling exaggerates it
        self.assertLess(max(fixed) - min(fixed), 1.5)   # 0..20 keeps it honest

    def test_flat_series_is_a_centred_line(self):
        self.assertEqual(set(ys(history.sparkline([(1, 2), (2, 2), (3, 2)]))), {14.0})

    def test_every_point_has_a_hover_label_and_the_last_is_now(self):
        svg = history.sparkline([(0, 1), (60, 2), (120, 3)], fmt=lambda v: f"{v} players")
        titles = re.findall(r"<title>([^<]+)</title>", svg)
        self.assertEqual(len(titles), 3)
        self.assertEqual(titles[-1], "now: 3 players")
        self.assertEqual(svg.count("class=now"), 1)       # only the live point is marked

    def test_values_outside_a_fixed_range_are_clamped(self):
        y = ys(history.sparkline([(1, -5), (2, 50)], lo=0, hi=20))
        self.assertEqual(y, [24.0, 4.0])  # bottom and top padding, not off-canvas


if __name__ == "__main__":
    unittest.main()
