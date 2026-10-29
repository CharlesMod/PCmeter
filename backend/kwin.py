"""Gadget placement on KDE Plasma (Wayland) through a small KWin script.

A Wayland app cannot place its own windows or learn where they are, so PCmeter loads a KWin
script for the life of the process. It puts each gadget where it was last left (or at the
top-right of the active screen the first time), keeps the gadgets off the taskbar, the task
switcher and the pager, applies "keep on top", and reports every finished move back over D-Bus
so the position can be saved. The script is transient: it is unloaded on quit, and a stale copy
from a crash is replaced on the next start.
"""
import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtDBus import QDBusConnection, QDBusMessage

PLUGIN = "pcmeter-gadgets"

SCRIPT = r"""
const SERVICE = %(service)s, PATH = %(path)s, IFACE = %(iface)s;
const ABOVE = %(above)s;
const saved = %(saved)s;   // caption -> [x, y]
const order = %(order)s;   // captions, right to left, for first-time placement

function mine(w) { return w && w.resourceClass == %(app)s && order.indexOf(w.caption) >= 0; }

function place(w) {
  const g = w.frameGeometry;
  let p = saved[w.caption];
  if (!p) {
    const area = workspace.clientArea(KWin.PlacementArea, workspace.activeScreen, workspace.currentDesktop);
    const i = order.indexOf(w.caption);
    p = [area.x + area.width - 24 - (i + 1) * g.width - i * 16, area.y + 24];
  }
  w.frameGeometry = { x: p[0], y: p[1], width: g.width, height: g.height };
}

function setup(w) {
  if (!mine(w)) return;
  w.skipTaskbar = true;
  w.skipSwitcher = true;
  w.skipPager = true;
  w.onAllDesktops = true;
  w.keepAbove = ABOVE;
  place(w);
  w.interactiveMoveResizeFinished.connect(function () {
    const g = w.frameGeometry;
    saved[w.caption] = [Math.round(g.x), Math.round(g.y)];
    callDBus(SERVICE, PATH, IFACE, "moved", w.caption, Math.round(g.x), Math.round(g.y));
  });
}

workspace.windowList().forEach(setup);
workspace.windowAdded.connect(setup);
"""


def _call(service, path, iface, method, *args):
    msg = QDBusMessage.createMethodCall(service, path, iface, method)
    if args:
        msg.setArguments(list(args))
    return QDBusConnection.sessionBus().call(msg)


def present():
    bus = QDBusConnection.sessionBus()
    return bus.isConnected() and bus.interface().isServiceRegistered("org.kde.KWin").value()


class Placer:
    def __init__(self, app_id, service, path, iface):
        self.app_id, self.service, self.path, self.iface = app_id, service, path, iface
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or Path.home() / ".cache/pcmeter")
        cache.mkdir(parents=True, exist_ok=True)
        self.file = cache / "kwin-gadgets.js"
        self.loaded = False

    def load(self, order, saved, above):
        """(Re)load the script. `order` lists window captions right to left; `saved` maps caption -> (x, y)."""
        self.unload()
        self.file.write_text(SCRIPT % {
            "service": json.dumps(self.service), "path": json.dumps(self.path), "iface": json.dumps(self.iface),
            "app": json.dumps(self.app_id), "above": "true" if above else "false",
            "saved": json.dumps({k: list(v) for k, v in saved.items()}), "order": json.dumps(list(order)),
        })
        reply = _call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript", str(self.file), PLUGIN)
        args = reply.arguments()
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not args or int(args[0]) < 0:
            return False
        script_id = int(args[0])
        # Plasma 6 exposes each script at /Scripting/Script<N>; Plasma 5 used /<N>.
        for path in (f"/Scripting/Script{script_id}", f"/{script_id}"):
            run = _call("org.kde.KWin", path, "org.kde.kwin.Script", "run")
            if run.type() == QDBusMessage.MessageType.ReplyMessage:
                self.loaded = True
                return True
        return False

    def unload(self):
        _call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", PLUGIN)
        self.loaded = False
