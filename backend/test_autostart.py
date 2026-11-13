import json, os, plistlib, sys, tempfile, threading, types, unittest, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autostart as au
import pcmeter as pm


class FakeWinreg(types.ModuleType):
    """Enough of winreg for the Run key, kept in a dict."""
    HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = 1, 1, 2, 1

    def __init__(self):
        super().__init__("winreg")
        self.values = {}

    class _Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def OpenKey(self, root, path, reserved, access):
        assert root == self.HKEY_CURRENT_USER and path == au.RUN_KEY
        return self._Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"HOME": self.tmp.name, "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.args = mock.patch.object(au, "launch_args", return_value=["/opt/My Apps/pcmeter", "--app"])
        self.args.start()

    def tearDown(self):
        self.args.stop()
        self.env.stop()
        self.tmp.cleanup()


class Linux(Home):
    def test_entry_written_quoted_and_removed(self):
        self.assertFalse(au.enabled("Linux"))
        au.set_enabled(True, "Linux")
        self.assertTrue(au.enabled("Linux"))
        text = au.xdg_file().read_text()
        self.assertIn('Exec="/opt/My Apps/pcmeter" --app\n', text)
        self.assertTrue(str(au.xdg_file()).startswith(os.path.join(self.tmp.name, "cfg", "autostart")))
        au.set_enabled(False, "Linux")
        self.assertFalse(au.enabled("Linux"))
        au.set_enabled(False, "Linux")   # already off is fine

    def test_exec_quoting(self):
        self.assertEqual(au._exec_quote("--app"), "--app")
        self.assertEqual(au._exec_quote('a "b" $c'), '"a \\"b\\" \\$c"')


class MacOS(Home):
    def test_launch_agent(self):
        au.set_enabled(True, "Darwin")
        self.assertTrue(au.enabled("Darwin"))
        plist = plistlib.loads(au.launch_agent_file().read_bytes())
        self.assertEqual(plist["ProgramArguments"], ["/opt/My Apps/pcmeter", "--app"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["Label"], au.LABEL)
        au.set_enabled(False, "Darwin")
        self.assertFalse(au.enabled("Darwin"))


class Windows(Home):
    def test_run_key(self):
        reg = FakeWinreg()
        with mock.patch.dict(sys.modules, {"winreg": reg}):
            self.assertFalse(au.enabled("Windows"))
            au.set_enabled(True, "Windows")
            self.assertEqual(reg.values["PCmeter"], '"/opt/My Apps/pcmeter" --app')
            self.assertTrue(au.enabled("Windows"))
            au.set_enabled(False, "Windows")
            self.assertFalse(au.enabled("Windows"))
            au.set_enabled(False, "Windows")


class Unsupported(unittest.TestCase):
    def test_other_systems(self):
        self.assertFalse(au.supported("Plan9"))
        self.assertFalse(au.enabled("Plan9"))
        with self.assertRaises(OSError):
            au.set_enabled(True, "Plan9")


class Endpoint(Home):
    """Only PCmeter's own page (or a local tool) may change the setting."""

    def setUp(self):
        super().setUp()
        self.system = mock.patch.object(au.platform, "system", return_value="Linux")
        self.system.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), pm.make_handler(object()))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.system.stop()
        super().tearDown()

    def post(self, body, origin=None, ctype="application/json"):
        req = urllib.request.Request(f"{self.base}/autostart", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": ctype, **({"Origin": origin} if origin else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_get_reports_state_without_cors(self):
        with urllib.request.urlopen(f"{self.base}/autostart") as r:
            self.assertEqual(json.loads(r.read()), {"supported": True, "enabled": False})
            self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))

    def test_own_page_can_toggle(self):
        self.assertEqual(self.post({"enabled": True}, origin=self.base), (200, {"supported": True, "enabled": True}))
        self.assertTrue(au.xdg_file().is_file())
        self.assertEqual(self.post({"enabled": False})[1]["enabled"], False)

    def test_other_sites_and_bad_requests_refused(self):
        self.assertEqual(self.post({"enabled": True}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.post({"enabled": True}, ctype="text/plain")[0], 403)
        self.assertEqual(self.post({"enabled": "yes"})[0], 400)
        self.assertFalse(au.xdg_file().exists())


if __name__ == "__main__":
    unittest.main()
