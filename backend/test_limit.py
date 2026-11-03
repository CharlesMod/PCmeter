import json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcmeter as pm


class Terms(unittest.TestCase):
    def test_power(self):
        self.assertAlmostEqual(pm.power_term(75.4, 120), 62.83, 1)
        self.assertEqual(pm.power_term(300, 120), 100.0)          # clamped
        self.assertIsNone(pm.power_term(None, 120))
        self.assertIsNone(pm.power_term(75, None))
        self.assertIsNone(pm.power_term(75, 0))

    def test_thermal(self):
        self.assertEqual(pm.thermal_term(100, 100), 100.0)
        self.assertEqual(pm.thermal_term(40, 100), 0.0)
        self.assertEqual(pm.thermal_term(30, 100), 0.0)           # below the idle reference
        self.assertEqual(pm.thermal_term(70, 100), 50.0)
        self.assertAlmostEqual(pm.thermal_term(94, 100), 90.0)
        self.assertEqual(pm.thermal_term(120, 100), 100.0)
        self.assertIsNone(pm.thermal_term(None, 100))
        self.assertIsNone(pm.thermal_term(70, None))
        self.assertIsNone(pm.thermal_term(70, 40))                # degenerate limit


class Pick(unittest.TestCase):
    def test_max_and_name(self):
        self.assertEqual(pm.pick_limiter({"POWER": 60, "THERMAL": 90, "LOAD": 25}), (90, "THERMAL"))
        self.assertEqual(pm.pick_limiter({"POWER": 95, "THERMAL": 90, "LOAD": 25}), (95, "POWER"))
        self.assertEqual(pm.pick_limiter({"POWER": 10, "THERMAL": 20, "LOAD": 99}), (99, "LOAD"))

    def test_ties(self):
        self.assertEqual(pm.pick_limiter({"POWER": 50, "THERMAL": 50, "LOAD": 50})[1], "POWER")
        self.assertEqual(pm.pick_limiter({"POWER": None, "THERMAL": 50, "LOAD": 50})[1], "THERMAL")

    def test_missing(self):
        self.assertEqual(pm.pick_limiter({"POWER": None, "THERMAL": None, "LOAD": 25}), (25, "LOAD"))
        self.assertEqual(pm.pick_limiter({}), (None, None))

    def test_info_host_like(self):
        i = pm.limit_info(25, 94, 100, 75.4, 120, True)
        self.assertEqual(i["term"], "THERMAL")
        self.assertAlmostEqual(i["value"], 90.0)
        self.assertTrue(i["throttled"])

    def test_info_no_power_no_temp(self):
        i = pm.limit_info(25, None, None, None, None, False)
        self.assertEqual((i["term"], i["value"]), ("LOAD", 25.0))
        self.assertIsNone(i["terms"]["POWER"])


class Throttle(unittest.TestCase):
    def test_delta(self):
        w = pm.ThrottleWatch(hold=3)
        self.assertFalse(w.update(1000, 0.0))      # first reading: no delta yet
        self.assertFalse(w.update(1000, 0.5))
        self.assertTrue(w.update(1400, 1.0))       # rose
        self.assertTrue(w.update(1400, 2.5))       # held
        self.assertFalse(w.update(1400, 4.5))      # hold expired
        self.assertTrue(w.update(1500, 5.0))

    def test_missing_counter(self):
        w = pm.ThrottleWatch()
        self.assertFalse(w.update(None, 0.0))
        self.assertFalse(w.update(None, 1.0))

    def test_counter_reset_is_not_a_rise(self):
        w = pm.ThrottleWatch()
        w.update(500, 0.0)
        self.assertFalse(w.update(10, 1.0))


class Sources(unittest.TestCase):
    def test_pl1(self):
        with tempfile.TemporaryDirectory() as d:
            for i, (n, uw) in enumerate([("long_term", 120000000), ("short_term", 241000000)]):
                (Path(d) / f"constraint_{i}_name").write_text(n + "\n")
                (Path(d) / f"constraint_{i}_power_limit_uw").write_text(f"{uw}\n")
            self.assertEqual(pm.rapl_pl1_watts(d), 120.0)
            self.assertIsNone(pm.rapl_pl1_watts(d + "/nope"))

    def test_fanctl_power(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            now = 1000.0
            fc = pm.FanctlCoolant(str(p), wall=lambda: now)
            self.assertIsNone(fc.read_input("power"))             # missing
            p.write_text(json.dumps({"t": 990, "inputs": {"power": 75.4, "coolant": 55}}))
            self.assertEqual(fc.read_input("power"), 75.4)
            self.assertEqual(fc.read(), 55.0)
            p.write_text(json.dumps({"t": 900, "inputs": {"power": 75.4}}))
            self.assertIsNone(fc.read_input("power"))             # stale
            p.write_text(json.dumps({"t": 995, "inputs": {}}))
            self.assertIsNone(fc.read_input("power"))             # key missing
            p.write_text("{nope")
            self.assertIsNone(fc.read_input("power"))             # malformed


if __name__ == "__main__":
    unittest.main()
