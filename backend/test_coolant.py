import os, stat, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcmeter as pm


def chip(root, n, name, temps):
    """temps: {index: (label or None, millidegrees)}"""
    d = Path(root) / f"hwmon{n}"
    d.mkdir(parents=True)
    (d / "name").write_text(name + "\n")
    for i, (label, val) in temps.items():
        (d / f"temp{i}_input").write_text(f"{val}\n")
        if label:
            (d / f"temp{i}_label").write_text(label + "\n")


class Hwmon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def sensor(self):
        return pm.CoolantSensor(hwmon_root=self.root, system="Linux", liquidctl="none")

    def test_labelled(self):
        chip(self.root, 0, "k10temp", {1: ("Tctl", 55000)})
        chip(self.root, 1, "whatever", {1: ("Coolant temp", 31500)})
        t, src = self.sensor().read()
        self.assertEqual((t, src), (31.5, "whatever"))

    def test_known_chip_unlabelled(self):
        chip(self.root, 0, "x53", {1: (None, 29000)})
        self.assertEqual(self.sensor().read(), (29.0, "x53"))

    def test_implausible_ignored(self):
        chip(self.root, 0, "d5next", {1: (None, 0)})
        chip(self.root, 1, "x", {1: ("Liquid", 120000)})
        self.assertEqual(self.sensor().read(), (None, None))

    def test_cpu_only(self):
        chip(self.root, 0, "k10temp", {1: ("Tctl", 55000)})
        s = self.sensor()
        self.assertEqual(s.read(), (None, None))
        f = s.fields()
        self.assertIsNone(f["coolant_temp"])
        self.assertIsNone(f["coolant_source"])
        self.assertEqual(f["temp_source"], "cpu")

    def test_rescan(self):
        t = [0.0]
        s = pm.CoolantSensor(hwmon_root=self.root, system="Linux", liquidctl="none", clock=lambda: t[0])
        self.assertEqual(s.read(), (None, None))
        chip(self.root, 0, "x53", {1: (None, 30000)})
        self.assertEqual(s.read(), (None, None))
        t[0] = 61
        self.assertEqual(s.read(), (30.0, "x53"))


JSON = ('[{"description": "Corsair Hydro H100i v2", "status": [{"key": "Fan 1 speed", "value": 900, "unit": "rpm"},'
        ' {"key": "Liquid temperature", "value": 30.4, "unit": "°C"}]}]')


class Liquidctl(unittest.TestCase):
    def script(self, body):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "liquidctl"
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)

    def test_parse(self):
        self.assertEqual(pm.parse_liquidctl_status(JSON), (30.4, "Corsair Hydro H100i v2"))
        self.assertIsNone(pm.parse_liquidctl_status("garbage"))
        self.assertIsNone(pm.parse_liquidctl_status("[]"))

    def test_poll_ok_and_stale(self):
        t = [100.0]
        p = pm.LiquidctlPoller(self.script(f"echo '{JSON}'\n"), clock=lambda: t[0])
        self.assertTrue(p.poll_once())
        self.assertEqual(p.latest(), (30.4, "liquidctl: Corsair Hydro H100i v2"))
        t[0] += 21
        self.assertEqual(p.latest(), (None, None))

    def test_failing_script_and_backoff(self):
        p = pm.LiquidctlPoller(self.script("exit 1\n"))
        for _ in range(5):
            self.assertFalse(p.poll_once())
        self.assertEqual(p.latest(), (None, None))
        self.assertEqual(p.delay(), 60.0)

    def test_missing_binary(self):
        p = pm.LiquidctlPoller("/nonexistent/liquidctl")
        self.assertFalse(p.poll_once())

    def test_sensor_uses_poller(self):
        with tempfile.TemporaryDirectory() as root:
            s = pm.CoolantSensor(hwmon_root=root, system="Linux", liquidctl=self.script(f"echo '{JSON}'\n"),
                                 start_thread=False)
            self.assertIsNotNone(s.poller)
            s.poller.poll_once()
            self.assertEqual(s.read()[0], 30.4)


class Selection(unittest.TestCase):
    def test_pick(self):
        self.assertEqual(pm.pick_temp_source(30.0, "auto"), "coolant")
        self.assertEqual(pm.pick_temp_source(30.0, "coolant"), "coolant")
        self.assertEqual(pm.pick_temp_source(30.0, "cpu"), "cpu")
        self.assertEqual(pm.pick_temp_source(None, "auto"), "cpu")
        self.assertEqual(pm.pick_temp_source(None, "coolant"), "cpu")

    def test_fields_preference(self):
        with tempfile.TemporaryDirectory() as root:
            chip(root, 0, "x53", {1: (None, 30000)})
            for pref, want in (("auto", "coolant"), ("coolant", "coolant"), ("cpu", "cpu")):
                s = pm.CoolantSensor(pref, "none", hwmon_root=root, system="Linux")
                f = s.fields()
                self.assertEqual(f["temp_source"], want)
                self.assertEqual(f["coolant_temp"], 30.0)


if __name__ == "__main__":
    unittest.main()
