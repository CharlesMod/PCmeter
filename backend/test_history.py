import json, os, sys, tempfile, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcmeter as pm


class Ring(unittest.TestCase):
    def test_average_and_nulls(self):
        h = pm.History()
        h.add(100.0, 30.0, 50.0, None)
        h.add(102.0, 32.0, None, None)
        h.add(105.0, None, 60.0, None)   # closes the bucket
        self.assertEqual(h.payload()["samples"], [[105.0, 31.0, 55.0, None]])

    def test_all_null_and_second_bucket(self):
        h = pm.History()
        h.add(0.0, None, None, None)
        h.add(5.0, None, None, None)
        h.add(6.0, 1.0, 2.0, 3.0)
        h.add(11.0, 3.0, 4.0, 5.0)
        self.assertEqual(h.payload()["samples"], [[5.0, None, None, None], [11.0, 2.0, 3.0, 4.0]])

    def test_maxlen(self):
        h = pm.History(bucket=1, maxlen=3)
        for i in range(10):
            h.add(float(i), 1.0, 1.0, 1.0)
        s = h.payload()["samples"]
        self.assertEqual(len(s), 3)
        self.assertEqual(s[-1][0], 9.0)
        self.assertEqual(pm.History.MAXLEN, 1440)

    def test_raw_minute(self):
        h = pm.History()
        for i in range(400):                       # 0.5 s samples for 200 s
            h.add(i * 0.5, 30.0 + i * 0.01, None, 40.0)
        raw = h.payload()["raw"]
        self.assertEqual(raw[-1], [199.5, 33.99, None, 40.0])          # nothing averaged
        self.assertEqual(raw[1][0] - raw[0][0], 0.5)                     # every sample kept
        self.assertLessEqual(raw[-1][0] - raw[0][0], pm.History.RAW_SECONDS)
        self.assertGreaterEqual(raw[-1][0] - raw[0][0], 120)             # covers the 1 minute window with room
        self.assertEqual(len(h.payload()["samples"]), 36)                # the 5 s buckets are unchanged

    def test_events_window(self):
        h = pm.History(bucket=1)
        h.event(1.0, True, "cpu")
        h.add(10.0, 1, 1, 1); h.add(11.0, 1, 1, 1)
        h.event(12.0, False, None)
        self.assertEqual(h.payload()["events"], [{"t": 12.0, "on": False, "trigger": None}])


class Fan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")
        self.h = pm.History()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **d):
        with open(self.path, "w") as f:
            json.dump(d, f)

    def test_old_single_fan_shape(self):
        # fanctl before the Arctic AIO: one fan of any name; it counts as the radiator, there is no pump
        self.write(t=100, override=True, trigger="coolant",
                   fans={"a": {"duty": 80, "rpm": 1200}, "b": {"duty": 1, "rpm": 2}})
        f = pm.FanState(self.path, self.h)
        self.assertEqual(f.read(101), {"override": True, "trigger": "coolant", "duty": 80, "rpm": 1200,
                                       "radiator": {"duty": 80, "rpm": 1200}, "pump": None})

    def test_new_shape_by_name(self):
        # the pump listed first must not be taken for the radiator
        self.write(t=100, override=False, trigger=None,
                   fans={"pump": {"duty": None, "rpm": 1820, "watch": True, "stalled": False},
                         "radiator": {"duty": 52, "rpm": 1710}},
                   inputs={"cpu": 61.2, "power": 88.0, "gpu": 40.1, "nvme": 47.9})
        v = pm.FanState(self.path, self.h).read(101)
        self.assertEqual(v["radiator"], {"duty": 52, "rpm": 1710})
        self.assertEqual((v["duty"], v["rpm"]), (52, 1710))           # old "fan" key = the radiator
        self.assertEqual(v["pump"], {"duty": None, "rpm": 1820, "stalled": False})
        self.assertFalse(v["override"])

    def test_pump_stall(self):
        self.write(t=100, override=True, trigger="pump",
                   fans={"radiator": {"duty": 100, "rpm": 2200},
                         "pump": {"duty": None, "rpm": 0, "watch": True, "stalled": True}})
        f = pm.FanState(self.path, self.h)
        v = f.read(101)
        self.assertTrue(v["pump"]["stalled"])
        self.assertEqual(v["trigger"], "pump")
        self.assertEqual(self.h.events[0]["trigger"], "pump")

    def test_pump_only_or_odd_fans(self):
        self.write(t=100, override=False, trigger=None, fans={"pump": {"rpm": 1800}})
        v = pm.FanState(self.path, self.h).read(101)
        self.assertIsNone(v["radiator"])
        self.assertIsNone(v["duty"])
        self.assertEqual(v["pump"]["rpm"], 1800)
        self.write(t=100, override=False, trigger=7, fans={"radiator": "x", "pump": 3})
        v = pm.FanState(self.path, self.h).read(101)
        self.assertIsNone(v["trigger"])
        self.assertIsNone(v["radiator"])
        self.assertIsNone(v["pump"])

    def test_stale_missing_none(self):
        self.write(t=100, override=False, trigger=None, fans={})
        self.assertIsNone(pm.FanState(self.path, self.h).read(116))
        self.assertEqual(pm.FanState(self.path, self.h).read(114)["duty"], None)
        self.assertIsNone(pm.FanState(os.path.join(self.tmp.name, "no"), self.h).read(1))
        self.assertIsNone(pm.FanState("none", self.h).read(1))
        with open(self.path, "w") as f:
            f.write("{bad")
        self.assertIsNone(pm.FanState(self.path, self.h).read(1))

    def test_mtime_fallback(self):
        self.write(override=False, trigger=None, fans={})
        mt = os.stat(self.path).st_mtime
        self.assertIsNotNone(pm.FanState(self.path, self.h).read(mt + 5))
        self.assertIsNone(pm.FanState(self.path, self.h).read(mt + 60))

    def test_events_and_throttle(self):
        f = pm.FanState(self.path, self.h)
        self.write(t=100, override=False, trigger=None, fans={})
        f.read(100)                                   # off at first sight: no event
        self.write(t=101, override=True, trigger="cpu", fans={})
        f.read(101)                                   # inside 2 s: cached
        self.assertEqual(len(self.h.events), 0)
        f.read(103)
        self.write(t=104, override=True, trigger="cpu", fans={})
        f.read(106)                                   # unchanged: no new event
        self.write(t=107, override=False, trigger=None, fans={})
        f.read(109)
        self.assertEqual([(e["on"], e["trigger"]) for e in self.h.events], [(True, "cpu"), (False, None)])

    def test_first_observation_on(self):
        self.write(t=100, override=True, trigger="coolant", fans={})
        pm.FanState(self.path, self.h).read(100)
        self.assertEqual(len(self.h.events), 1)


class Http(unittest.TestCase):
    def test_history_shape(self):
        class T:
            history = pm.History(bucket=1)
        T.history.add(1.0, 30, 40, 50); T.history.add(2.5, 30, 40, 50)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.make_handler(T))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/history"))
        finally:
            srv.shutdown(); srv.server_close()
        self.assertEqual(d, {"interval": 1, "samples": [[2.5, 30.0, 40.0, 50.0]], "events": [],
                             "raw": [[1.0, 30, 40, 50], [2.5, 30, 40, 50]]})


if __name__ == "__main__":
    unittest.main()
