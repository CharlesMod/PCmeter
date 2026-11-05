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

    def test_auto_poller_gives_up_without_a_device(self):
        p = pm.LiquidctlPoller(self.script("exit 1\n"), give_up=True)
        for _ in range(pm.LiquidctlPoller.GIVE_UP_AFTER):
            self.assertFalse(p.stopped)
            p.poll_once()
        self.assertTrue(p.stopped)
        p.run()                                  # returns at once instead of looping forever

    def test_auto_poller_keeps_going_once_it_has_worked(self):
        p = pm.LiquidctlPoller(self.script(f"echo '{JSON}'\n"), give_up=True)
        self.assertTrue(p.poll_once())
        p.path = "/nonexistent/liquidctl"
        for _ in range(10):
            p.poll_once()
        self.assertFalse(p.stopped)

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


class FanctlState(unittest.TestCase):
    """fanctl's state file is the first coolant source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "state.json")
        self.now = 1000.0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        Path(self.state).write_text(text)

    def sensor(self, hwmon=None):
        return pm.CoolantSensor(hwmon_root=hwmon or self.tmp.name + "/none", system="Linux", liquidctl="none",
                                fanctl=self.state, wall=lambda: self.now)

    def test_fresh(self):
        self.write('{"t": 990.0, "inputs": {"cpu": 80, "coolant": 55.53}}')
        self.assertEqual(self.sensor().read(), (55.53, "fanctl"))
        self.assertEqual(self.sensor().fields()["temp_source"], "coolant")

    def test_stale_falls_back_to_hwmon(self):
        self.write('{"t": 900.0, "inputs": {"coolant": 55.5}}')
        root = self.tmp.name + "/hw"
        chip(root, 0, "x53", {1: (None, 29000)})
        self.assertEqual(self.sensor(root).read(), (29.0, "x53"))

    def test_stale_without_fallback(self):
        self.write('{"t": 900.0, "inputs": {"coolant": 55.5}}')
        self.assertEqual(self.sensor().read(), (None, None))

    def test_missing_key(self):
        self.write('{"t": 995.0, "inputs": {"cpu": 80}}')
        self.assertEqual(self.sensor().read(), (None, None))

    def test_malformed(self):
        for text in ("{not json", "[]", '{"t": 995.0, "inputs": {"coolant": "hot"}}', '{"t": 995.0, "inputs": 5}', ""):
            self.write(text)
            self.assertEqual(self.sensor().read(), (None, None), text)

    def test_implausible(self):
        self.write('{"t": 995.0, "inputs": {"coolant": 3.0}}')
        self.assertEqual(self.sensor().read(), (None, None))

    def test_missing_file(self):
        self.assertEqual(self.sensor().read(), (None, None))

    def test_follows_updates_and_age(self):
        self.write('{"t": 995.0, "inputs": {"coolant": 40.5}}')
        s = self.sensor()
        self.assertEqual(s.read(), (40.5, "fanctl"))
        self.write('{"t": 996.0, "inputs": {"coolant": 41.25}}')
        self.assertEqual(s.read(), (41.25, "fanctl"))
        self.now = 1040.0                       # 44 s old now
        self.assertEqual(s.read(), (None, None))

    def test_no_coolant_in_new_state_falls_back_to_cpu(self):
        # Arctic Liquid Freezer III Pro: fanctl state without inputs.coolant
        self.write('{"t": 995.0, "override": false, "trigger": null, "fans": {}, '
                   '"inputs": {"cpu": 61.2, "power": 88.0, "gpu": 40.1, "nvme": 47.9}}')
        s = pm.CoolantSensor(hwmon_root=self.tmp.name + "/none", system="Linux", liquidctl="/bin/true",
                             fanctl=self.state, start_thread=False, wall=lambda: self.now)
        self.assertIsNone(s.poller)             # fanctl owns the loop: no liquidctl polling for a missing sensor
        f = s.fields()
        self.assertIsNone(f["coolant_temp"])
        self.assertIsNone(f["coolant_source"])
        self.assertEqual(f["temp_source"], "cpu")

    def test_no_second_liquidctl_client(self):
        self.write('{"t": 995.0, "inputs": {"coolant": 40.5}}')
        s = pm.CoolantSensor(hwmon_root=self.tmp.name + "/none", system="Linux", liquidctl="/bin/true",
                             fanctl=self.state, start_thread=False)
        self.assertIsNone(s.poller)
