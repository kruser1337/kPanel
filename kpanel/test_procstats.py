"""CPU and memory from a fake /proc.

    cd kpanel && python -m unittest test_procstats -v
"""

import os
import tempfile
import unittest
from unittest import mock

import procstats


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def stat_line(pid, comm, utime, stime):
    # stat(5): pid (comm) state ppid ... field 14 = utime, 15 = stime
    rest = ["S", "1"] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 30
    return f"{pid} ({comm}) " + " ".join(rest) + "\n"


class FakeProc(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.mkdtemp()
        # pid 1: the image's init; pid 7: java; pid 9: something with a nasty name
        write(f"{self.proc}/1/comm", "mc-server-runne\n")
        write(f"{self.proc}/1/stat", stat_line(1, "mc-server-runne", 5, 5))
        write(f"{self.proc}/9/comm", "a) b (c\n")
        write(f"{self.proc}/9/stat", stat_line(9, "a) b (c", 1, 1))
        self.set_java(ticks=(1000, 200))
        write(f"{self.proc}/7/status", "Name:\tjava\nVmPeak:\t 9999 kB\nVmRSS:\t 1468006 kB\n")
        write(f"{self.proc}/meminfo", "MemTotal:  8135680 kB\nMemFree: 300000 kB\nMemAvailable: 3658752 kB\n")

    def set_java(self, ticks):
        write(f"{self.proc}/7/comm", "java\n")
        write(f"{self.proc}/7/stat", stat_line(7, "java", *ticks))

    def test_finds_the_jvm(self):
        self.assertEqual(procstats.find_java(self.proc), 7)

    def test_cpu_ticks_survive_parens_and_spaces_in_comm(self):
        self.assertEqual(procstats.cpu_ticks(7, self.proc), 1200)
        self.assertEqual(procstats.cpu_ticks(9, self.proc), 2)

    def test_memory(self):
        m = procstats.memory(self.proc)
        self.assertEqual(m["rss"], 1468006 * 1024)
        self.assertEqual(m["total"], 8135680 * 1024)
        self.assertEqual(m["available"], 3658752 * 1024)

    def test_cpu_percent_is_the_rate_since_the_last_reading_across_all_cores(self):
        meter = procstats.CpuMeter(proc=self.proc, ncpu=4, clk=100, window=0)
        clock = iter([100.0, 100.0, 110.0])
        with mock.patch.object(procstats.time, "monotonic", lambda: next(clock)), \
             mock.patch.object(procstats.time, "sleep", lambda s: None):
            meter.percent()                       # first reading: establishes the baseline
            self.set_java(ticks=(1000 + 400, 200))  # +400 ticks = 4 CPU-seconds
            pct = meter.percent()                 # 10 s later
        # 4 CPU-s over 10 s = 0.4 cores = 10% of 4 cores
        self.assertAlmostEqual(pct, 10.0)

    def test_no_jvm_means_none_not_an_error(self):
        empty = tempfile.mkdtemp()
        self.assertIsNone(procstats.find_java(empty))
        self.assertIsNone(procstats.CpuMeter(proc=empty).percent())
        self.assertIsNone(procstats.memory(empty)["rss"])

    def test_missing_proc_entirely(self):
        self.assertIsNone(procstats.find_java("/nonexistent/proc"))
        self.assertEqual(procstats.meminfo("/nonexistent/proc"), {})


if __name__ == "__main__":
    unittest.main()
