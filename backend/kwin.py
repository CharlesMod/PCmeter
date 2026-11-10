"""Gadget placement on KDE Plasma (Wayland) through a small KWin script.

A Wayland app cannot place its own windows or learn where they are, so PCmeter loads a KWin
script for the life of the process. It puts each gadget where it was last left (or at the
top-right of the active screen the first time (stacked flush, half the work area high)), keeps the gadgets off the taskbar, the task
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
const ABOVE = %(above)s, DEBUG = %(debug)s;
const saved = %(saved)s;   // caption -> [x, y]
const order = %(order)s;   // captions, right to left, for first-time placement
const SNAP_PX = %(snap_px)d, SNAP_FRACTION = %(snap_fraction)s;

function mine(w) { return w && w.resourceClass == %(app)s && order.indexOf(w.caption) >= 0; }

function areaOf(w) {
  const out = w.output || workspace.activeScreen;
  return workspace.clientArea(KWin.PlacementArea, out, workspace.currentDesktop);
}

// First-time placement: the cards stack flush in one column at the right edge of the work area
// (the screen minus panels), the first caption on top. Each is half the area high, so two fill it.
function place(w) {
  const g = w.frameGeometry;
  let p = saved[w.caption];
  if (!p) {
    const area = areaOf(w);
    const j = order.length - 1 - order.indexOf(w.caption);
    p = [area.x + area.width - g.width, area.y + j * Math.floor(area.height / order.length)];
  }
  if (Math.round(g.x) == Math.round(p[0]) && Math.round(g.y) == Math.round(p[1])) return;
  w.frameGeometry = { x: p[0], y: p[1], width: g.width, height: g.height };
}

// Tell PCmeter each screen's work area, so a card's default height can be half of it.
function report() {
  (workspace.screens || []).forEach(function (o) {
    const a = workspace.clientArea(KWin.PlacementArea, o, workspace.currentDesktop);
    callDBus(SERVICE, PATH, IFACE, "workArea", o.name, Math.round(a.x), Math.round(a.y), Math.round(a.width), Math.round(a.height));
  });
}

function setup(w) {
  if (!mine(w)) return;
  w.skipTaskbar = true;
  w.skipSwitcher = true;
  w.skipPager = true;
  w.onAllDesktops = true;
  w.keepAbove = ABOVE;
  place(w);
  // until the user moves it, keep the default stack in place while the card settles to its size
  // (never during or after a drag: a right-edge drag must leave the left edge where it is)
  let touched = false;
  // Width deadband: while an edge drag is within the band around the card's default width (half the
  // work area high), hold the window at exactly that width, so stacked cards line up. PCmeter supplies
  // the target when the drag starts; the edge not being dragged stays put.
  let start = null, target = 0;
  w.interactiveMoveResizeStarted.connect(function () {
    touched = true;
    start = w.frameGeometry;
    start = { x: start.x, y: start.y, width: start.width, height: start.height };
    target = 0;
    callDBus(SERVICE, PATH, IFACE, "resizeState", w.caption, true);
    callDBus(SERVICE, PATH, IFACE, "snapWidth", w.caption, function (t) { target = Number(t) || 0; });
  });
  try {
    w.interactiveMoveResizeStepped.connect(function (g) {
      if (!w.resize || !start || target <= 0) return;
      const band = Math.max(SNAP_PX, Math.round(target * SNAP_FRACTION));
      if (Math.abs(g.width - target) > band || Math.abs(g.width - target) < 0.5) return;
      const rightFixed = Math.abs((g.x + g.width) - (start.x + start.width)) < 1;
      const x = rightFixed ? g.x + g.width - target : g.x;
      w.frameGeometry = { x: x, y: g.y, width: target, height: g.height };
    });
  } catch (e) {}
  w.frameGeometryChanged.connect(function () {
    if (DEBUG) { const f = w.frameGeometry; callDBus(SERVICE, PATH, IFACE, "frame", w.caption, Math.round(f.x), Math.round(f.y), Math.round(f.x + f.width), Math.round(f.y + f.height)); }
    if (!touched && !saved[w.caption] && !w.move && !w.resize) place(w);
  });
  w.interactiveMoveResizeFinished.connect(function () {
    const g = w.frameGeometry;
    saved[w.caption] = [Math.round(g.x), Math.round(g.y)];
    callDBus(SERVICE, PATH, IFACE, "moved", w.caption, Math.round(g.x), Math.round(g.y));
    callDBus(SERVICE, PATH, IFACE, "resizeState", w.caption, false);
  });
}

report();
try { workspace.screensChanged.connect(report); } catch (e) {}
try { workspace.currentDesktopChanged.connect(report); } catch (e) {}
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

    def load(self, order, saved, above, debug=False, snap=(32, 0.08)):
        """(Re)load the script. `order` lists window captions right to left; `saved` maps caption -> (x, y);
        `snap` is the width deadband, (at least this many px, or this fraction of the target width)."""
        self.unload()
        self.file.write_text(SCRIPT % {
            "snap_px": int(snap[0]), "snap_fraction": repr(float(snap[1])),
            "service": json.dumps(self.service), "path": json.dumps(self.path), "iface": json.dumps(self.iface),
            "app": json.dumps(self.app_id), "above": "true" if above else "false", "debug": "true" if debug else "false",
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
