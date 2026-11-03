import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcmeter as pm


def row(pid, name, util, mem=10.0, exe=None):
    return {"pid": pid, "name": name, "exe": exe, "util": util, "mem_mb": mem}


class Grouping(unittest.TestCase):
    def test_same_exe_is_one_row(self):
        rows = [row(1, "Isolated Web Co", 20, 100, "firefox"), row(2, "firefox", 5, 300, "firefox"),
                row(3, "WebExtensions", 3, 50, "firefox"), row(4, "kwin_wayland", 60, 200, "kwin_wayland")]
        out = pm.group_procs(rows)
        self.assertEqual([g["name"] for g in out], ["kwin_wayland", "firefox"])
        ff = out[1]
        self.assertEqual((ff["n"], round(ff["util"], 1), ff["mem_mb"], ff["pid"]), (3, 28.0, 450.0, 1))

    def test_falls_back_to_name(self):
        out = pm.group_procs([row(1, "kworker/1:0", 2), row(2, "kworker/1:0", 3), row(3, "other", 1)])
        self.assertEqual([(g["name"], g["n"]) for g in out], [("kworker/1:0", 2), ("other", 1)])

    def test_over_one_core_and_limit(self):
        rows = [row(i, f"p{i}", 150 - i) for i in range(30)]
        out = pm.group_procs(rows, limit=10)
        self.assertEqual(len(out), 10)
        self.assertGreater(out[0]["util"], 100)           # % of one core, not of the machine
        self.assertNotIn("_top", out[0])


class Other(unittest.TestCase):
    def test_gap_row(self):
        procs = [{"pid": 1, "name": "a", "util": 100.0, "mem_mb": 1, "n": 1}, {"pid": 2, "name": "b", "util": 50.0, "mem_mb": 1, "n": 1}]
        out = pm.with_other(procs, 457.0)
        self.assertEqual(len(out), 3)
        self.assertTrue(out[2]["other"])
        self.assertAlmostEqual(out[2]["util"], 307.0)
        self.assertEqual(out[2]["n"], 0)

    def test_no_row_when_accounted_for_or_unknown(self):
        procs = [{"pid": 1, "name": "a", "util": 100.0, "mem_mb": 1, "n": 1}]
        self.assertEqual(pm.with_other(procs, 100.2), procs)
        self.assertEqual(pm.with_other(procs, 90.0), procs)
        self.assertEqual(pm.with_other(procs, None), procs)


class Scan(unittest.TestCase):
    def test_linux_scan_shapes(self):
        if not os.path.exists("/proc/stat"):
            self.skipTest("no /proc")
        s = pm.CpuSampler()
        s._scan_procs()
        s.last_scan -= 1.0
        s.prev_busy = (s.prev_busy or 0) - 100
        s._scan_procs()
        self.assertTrue(all({"pid", "name", "util", "mem_mb", "n"} <= set(p) for p in s.procs))
        self.assertLessEqual(len([p for p in s.procs if not p.get("other")]), 10)


if __name__ == "__main__":
    unittest.main()
