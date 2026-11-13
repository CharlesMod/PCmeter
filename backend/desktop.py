"""PCmeter desktop gadgets: the CPU and GPU cards as separate frameless, translucent windows.

There is no outer window. Each card is its own Qt Quick window (qml/Card.qml), exactly the size of
the card, and the desktop shows through it. Drag a card anywhere to move it, drag its left or right
edge to change its width (the height follows the content), and use its buttons to dismiss it or open
the menu. The menu (also in the system tray) picks the skin, the view (full, compact, gauges), clicks,
keep-on-top, launch on startup, and which cards are shown.

The cards cost nothing while nothing moves: the scene graph renders only when an animation runs, and
sampling stops entirely (the sampler waits on an event) while every card is hidden or minimised or
the session is locked. The telemetry reaches the windows in-process, with no server.

Wayland (KDE Plasma): windows stay native so they are sharp at fractional scales. KWin does
the placing (see kwin.py) and the blur behind each card (see wlblur.py).
X11: the windows place themselves and ask KWin for blur with _KDE_NET_WM_BLUR_BEHIND_REGION.

One copy runs per user: launching again re-shows any dismissed card (D-Bus org.pcmeter.PCmeter).
"""
import collections
import ctypes
import math
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import ClassInfo, Property, QObject, QPoint, QRect, QSettings, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QSurfaceFormat, QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from shiboken6 import getCppPointer, wrapInstance

import kwin
import wlblur
from autostart import enabled as autostart_enabled, set_enabled as set_autostart

APP_ID = "pcmeter"
SERVICE, OBJECT_PATH, IFACE = "org.pcmeter.PCmeter", "/PCmeter", "org.pcmeter.PCmeter"
CARDS = (("cpu", "CPU"), ("gpu", "GPU"))
SKINS = (("glass", "Glass"), ("matrix", "Matrix"), ("hive", "Hive"), ("windows", "Windows"),
         ("mac", "macOS"), ("linux", "Linux"), ("dark", "Dark"), ("white", "White"))
VIEWS = (("full", "Full"), ("compact", "Compact"), ("gauge", "Gauges only"))
DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH = 440, 340, 900
MIN_ZOOM, MAX_ZOOM = 0.5, 3.0
MIN_PX, MAX_PX = 240, 1320      # window width limits (the card scales, so these are zoom 0.55 and 3.0)
SNAP_PX, SNAP_FRACTION = 32, 0.08   # an edge drag released this close to the default width snaps to it
# The cards are drawn by Qt Quick. "gpu" (default) uses its hardware renderer (RHI: OpenGL or Vulkan): on
# a real desktop it idles near 0.3% of a core. "cpu" (PCMETER_RENDER=cpu) is the software renderer, for
# hosts with no usable GL (about 15% of a core when drawing a 4K card).
#   "gpu" (default): OpenGL, Qt's basic render loop (both cards drawn from the GUI thread) and NVIDIA's
#       usleep-based swap wait. Measured on an RTX 5080 / KWin Wayland, two cards at 60 fps each: threaded
#       loop 857 ticks / 30 s (28%), + __GL_YIELD=USLEEP 636 (21%), Vulkan threaded 522 (17%),
#       basic + USLEEP 348 (11.6%), Vulkan + basic 330 (11%). The render threads sit in eglSwapBuffers.
#   "threaded": the threaded loop (a render thread per window); "vulkan": the Vulkan RHI backend.
RENDER = os.environ.get("PCMETER_RENDER", "gpu")
BURST_MS = int(os.environ.get("PCMETER_BURST", "0"))
DEBUG = bool(os.environ.get("PCMETER_DEBUG"))
QML_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "qml"


def caption(label):
    return f"PCmeter {label}"


def rounded_rects(w, h, r):
    """A rounded rectangle as horizontal strips, for blur regions (which take only rectangles)."""
    r = max(0, min(int(math.ceil(r)), w // 2, h // 2))
    if r == 0:
        return [(0, 0, w, h)]
    rows = []
    for i in range(r):
        dy = r - i - 0.5
        inset = int(math.ceil(r - math.sqrt(max(0.0, r * r - dy * dy))))
        if rows and rows[-1][0] == inset:
            rows[-1][1] += 1
        else:
            rows.append([inset, 1])
    rects, y = [], 0
    for inset, n in rows:
        rects.append((inset, y, w - 2 * inset, n))
        rects.append((inset, h - y - n, w - 2 * inset, n))
        y += n
    rects.append((0, r, w, h - 2 * r))
    return rects


def is_true(v):
    return v in ("true", True)


def _unwrap(value):
    """A D-Bus variant's payload (PySide hands back QDBusVariant, or the plain value)."""
    return value.variant() if hasattr(value, "variant") else value


def _path(value):
    value = _unwrap(value)
    return str(value.path()) if hasattr(value, "path") else str(value)


class Feed(QObject):
    """The latest telemetry snapshot, as a property the cards bind to. `push` is called on the sampler thread."""
    snapshotChanged = Signal()
    _arrive = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snap = {}
        self._logged = 0.0
        self._arrive.connect(self._store)          # queued: delivered on the Qt thread
        self._avg = {}                             # key -> deque of (time, util): the dial's 3 s window
        self.history = None                        # the sampler's temperature History, set by the controller

    def push(self, snap):
        self._arrive.emit(snap)

    DIAL_WINDOW_S = 3.0

    def _smooth(self, key, util, now):
        """Mean of the samples in the last DIAL_WINDOW_S seconds (fewer at start-up)."""
        q = self._avg.setdefault(key, collections.deque())
        q.append((now, util))
        while len(q) > 1 and now - q[0][0] > self.DIAL_WINDOW_S:
            q.popleft()
        return sum(v for _, v in q) / len(q)

    @staticmethod
    def _dial_value(d):
        """What the dial shows: the closest limit (see pcmeter.limit_info), else plain utilisation."""
        v = (d.get("limit") or {}).get("value")
        return d["util"] if v is None else v

    @Slot(object)
    def _store(self, snap):
        now = time.monotonic()
        snap = dict(snap)                          # the dial's smoothed load rides along as util_avg
        cpu = snap.get("cpu")
        if cpu and cpu.get("util") is not None:
            snap["cpu"] = cpu = dict(cpu, util_avg=self._smooth("cpu", cpu["util"], now))
        gpus = snap.get("gpus")
        if gpus:
            snap["gpus"] = [dict(g, util_avg=self._smooth("gpu%d" % i, self._dial_value(g), now))
                            if g.get("util") is not None else g for i, g in enumerate(gpus)]
        if self.history is not None:               # a new 5 s temperature row: the Temps tab reloads
            with self.history.lock:
                snap["hist_t"] = self.history.samples[-1][0] if self.history.samples else 0
        self._snap = snap
        if DEBUG:
            ua = [cpu and cpu.get("util_avg")] + [g.get("util_avg") for g in snap.get("gpus") or []]
            print("DIAL t=%.2f raw=%s avg=%s" % (now % 1000, [cpu and cpu.get("util")] + [g.get("util") for g in snap.get("gpus") or []], ua),
                  file=sys.stderr, flush=True)
        if DEBUG and time.monotonic() - self._logged > 10:     # PCMETER_DEBUG=1: one summary line per 10 s
            self._logged = time.monotonic()
            cpu, gpus = snap.get("cpu"), snap.get("gpus") or []
            print("pcmeter: snapshot cpu=%s gpus=%s" % (cpu and "%s%%" % cpu.get("util"),
                  [g.get("name") or g.get("error") for g in gpus]), file=sys.stderr, flush=True)
        self.snapshotChanged.emit()

    snapshot = Property("QVariant", lambda self: self._snap, notify=snapshotChanged)

    @Slot(int, result="QVariant")
    def tempHistory(self, seconds):
        """The last `seconds` of the temperature history (5 s rows: t, coolant, die, gpu) and the fan events."""
        if self.history is None:
            return {"samples": [], "events": []}
        if seconds <= 120:                         # the 1 minute view: every sample, not 5 s averages
            return self.history.recent(seconds + 5)
        p = self.history.payload()
        rows = p["samples"]
        if rows:
            lo = rows[-1][0] - seconds - 60
            rows = [r for r in rows if r[0] >= lo]
            p["events"] = [e for e in p["events"] if e["t"] >= lo]
        p["samples"] = rows
        if DEBUG:
            print("pcmeter: temps history rows=%d events=%d last=%s" % (len(rows), len(p["events"]), rows[-1:] ), file=sys.stderr, flush=True)
        return p


class Bridge(QObject):
    """What a card may ask of its window, and the settings it shows. Edges use Qt.Edge bits: left 1, right 2."""
    skinChanged, viewChanged, soundChanged = Signal(), Signal(), Signal()
    activeChanged, mutedChanged, volumeChanged, foldChanged = Signal(), Signal(), Signal(), Signal()
    zoomChanged = Signal()

    def __init__(self, gadget):
        super().__init__(gadget)
        self.gadget = gadget
        self._active = False

    def _store_key(self, name):
        return f"{self.gadget.key}/{name}"

    # settings shared by both cards
    skin = Property(str, lambda self: self.gadget.ctl.skin, notify=skinChanged)
    view = Property(str, lambda self: self.gadget.ctl.view, notify=viewChanged)
    sound = Property(bool, lambda self: self.gadget.ctl.sound, notify=soundChanged)
    # the card is laid out at its natural size and scaled by `zoom` to fill its window
    zoom = Property(float, lambda self: self.gadget.zoom, notify=zoomChanged)
    burstMs = Property(int, lambda self: BURST_MS, constant=True)   # how long the animators run after each sample

    # the processor card's Live / Temps tab and the Temps time range (seconds); PCMETER_TAB=temps starts on Temps
    tabChanged, rangeChanged = Signal(), Signal()
    tab = Property(str, lambda self: str(self.gadget.ctl.store.value("cpu/tab", os.environ.get("PCMETER_TAB", "live"))),
                   lambda self, v: self._set_shared("cpu/tab", v, self.tabChanged), notify=tabChanged)
    trange = Property(int, lambda self: int(os.environ.get("PCMETER_RANGE") or self.gadget.ctl.store.value("cpu/range", 1800)),
                      lambda self, v: self._set_shared("cpu/range", v, self.rangeChanged), notify=rangeChanged)

    def _set_shared(self, key, value, signal):
        self.gadget.ctl.store.setValue(key, value)
        signal.emit()

    # on screen and awake: false while the card is hidden or minimised and while the session is locked
    active = Property(bool, lambda self: self._active, notify=activeChanged)

    def set_active(self, on):
        if on != self._active:
            self._active = on
            self.activeChanged.emit()

    # this card's own: clicks muted (cards start muted), click volume 0..1, process list folded out
    def _muted(self):
        return is_true(self.gadget.ctl.store.value(self._store_key("muted"), "true"))

    def _volume(self):
        return float(self.gadget.ctl.store.value(self._store_key("volume"), 0.6))

    def _fold(self):
        return is_true(self.gadget.ctl.store.value(self._store_key("fold"), "false"))

    def _set(self, name, value, signal):
        self.gadget.ctl.store.setValue(self._store_key(name), value)
        signal.emit()

    muted = Property(bool, _muted, lambda self, v: self._set("muted", v, self.mutedChanged), notify=mutedChanged)
    volume = Property(float, _volume, lambda self, v: self._set("volume", v, self.volumeChanged), notify=volumeChanged)
    fold = Property(bool, _fold, lambda self, v: self._set("fold", v, self.foldChanged), notify=foldChanged)

    @Slot()
    def startMove(self):
        self.gadget.startSystemMove()

    @Slot(int)
    def startResize(self, edges):
        # the card's own bits (left 1, right 2) are not Qt's (Qt.LeftEdge is 2, Qt.RightEdge is 4): the right
        # handle used to start a LEFT-edge resize and the left handle none at all
        qt_edges = Qt.Edges()
        if edges & 1:
            qt_edges |= Qt.Edge.LeftEdge
        if edges & 2:
            qt_edges |= Qt.Edge.RightEdge
        if qt_edges:
            self.gadget.mark_user_sized()
            self.gadget.begin_drag()
            self.gadget.startSystemResize(qt_edges)

    @Slot(int, int, float)
    def setSize(self, width, height, radius):
        """`width` 0 keeps the width the user chose; otherwise the view needs exactly that width."""
        self.gadget.content_size(width, height, radius)

    @Slot(bool)
    def setSound(self, on):
        self.gadget.ctl.sound_action.setChecked(on)

    @Slot(int, int)
    def showMenu(self, x, y):
        self.gadget.ctl.popup_menu(self.gadget, QPoint(x, y))

    @Slot()
    def dismiss(self):
        self.gadget.ctl.set_card(self.gadget.key, False)

    @Slot(result=bool)
    def closeRequested(self):
        """The window is closing (Alt+F4 and the like): dismiss the card instead, unless PCmeter is quitting."""
        if self.gadget.ctl.quitting:
            return True
        self.gadget.ctl.set_card(self.gadget.key, False)
        return False


class Gadget(QObject):
    """One card: a facade over a native Qt Quick window created by qml/GadgetWindow.qml.

    The window is deliberately not a Python subclass of QQuickView. PySide takes the GIL on every virtual
    call of a Python-derived object, from whichever thread makes it; the GUI thread holds the GIL while it
    waits for the render thread, so a threaded render loop deadlocks. A window made by the QML engine is
    plain C++, and the render thread never needs Python. Window methods not defined here (show, x, width,
    grabWindow...) are forwarded to it."""

    def __init__(self, ctl, key, label):
        super().__init__(ctl)
        self.ctl, self.key, self.label = ctl, key, label
        self.radius = 0.0
        self.fixed_width = False
        self.wanted = False          # should be on screen once the card has its size
        self.sized = False
        self.zoom = 1.0
        self.last = None             # the card's last report (width, height, radius), in its own layout units
        self.dragging = False        # an edge drag is in progress: KWin owns the geometry, the app sets none
        self.drag_idle = QTimer(self, singleShot=True, timeout=lambda: self.end_drag())
        stored = ctl.store.value(f"{key}/usersize")
        saved_w = ctl.store.value(f"{key}/width")
        # A size the keeper set by resizing is kept as saved. Older configs saved the width on every move,
        # so a width other than the old default counts as chosen.
        self.user = is_true(stored) if stored is not None else (saved_w is not None and int(saved_w) != DEFAULT_WIDTH)
        if self.user:
            self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(ctl.store.value(f"{key}/zoom", 1.0))))
        self.bridge = Bridge(self)
        component = QQmlComponent(ctl.engine, QUrl.fromLocalFile(str(QML_DIR / "GadgetWindow.qml")))
        made = component.createWithInitialProperties({"cardKey": key, "host": self.bridge, "feed": ctl.feed})
        self.win = wrapInstance(getCppPointer(made)[0], QQuickWindow)     # typed as a QQuickWindow (grabWindow)
        for error in component.errors():
            print(f"[{key}] {error.toString()}", file=sys.stderr, flush=True)
        self._component = component
        win = self.win
        win.setFlags(Gadget.flags_for(ctl))
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        win.setFormat(fmt)
        win.setTitle(caption(label))
        win.setIcon(ctl.icon)
        self.width_saved = QTimer(self, singleShot=True, interval=500, timeout=self._save_geometry)
        width = int(saved_w) if self.user and saved_w is not None else round(DEFAULT_WIDTH * self.zoom)
        win.setMinimumWidth(MIN_PX)
        win.setMaximumWidth(MAX_PX)
        win.resize(max(MIN_PX, min(MAX_PX, width)), 620)
        self.reveal = QTimer(self, singleShot=True, interval=2500, timeout=self._reveal)
        win.visibleChanged.connect(lambda _: ctl.update_presence())
        win.visibilityChanged.connect(lambda _: ctl.update_presence())
        win.widthChanged.connect(self._resized)
        win.heightChanged.connect(self._resized)
        win.xChanged.connect(self._moved)
        win.yChanged.connect(self._moved)
        win.screenChanged.connect(lambda _: self.rederive())

    def __getattr__(self, name):             # only reached for what Gadget does not define
        if name == "win":
            raise AttributeError(name)
        return getattr(self.win, name)

    @staticmethod
    def flags_for(ctl):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if not ctl.wayland:
            flags |= Qt.WindowType.Tool          # X11: a utility window, kept off the taskbar
            if ctl.above:
                flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    # ---- showing and hiding
    def open(self):
        self.wanted = True
        if self.sized:
            self._reveal()
        else:
            self.reveal.start()              # never wait forever for the card to report its size

    def _reveal(self):
        if not self.wanted or self.isVisible():
            return
        if not self.ctl.wayland:
            pos = self.ctl.store.value(f"{self.key}/pos")
            if pos is None:
                pos = self.ctl.default_position(self)
            self.setPosition(QPoint(int(pos[0]), int(pos[1])) if isinstance(pos, (list, tuple)) else pos)
        self.show()
        QTimer.singleShot(0, self.update_blur)
        QTimer.singleShot(250, self.update_blur)

    def dismiss(self):
        self.wanted = False
        if self.isVisible():
            self._save_geometry()
            if self.ctl.wayland:
                wlblur.clear_blur(self.win)
            self.hide()

    # ---- size, shape and blur
    def work_height(self):
        """Height of the work area (the screen without panels) this card is on."""
        screen = self.win.screen() or QGuiApplication.primaryScreen()
        return self.ctl.work_area(screen).height()

    def content_size(self, width, height, radius):
        """The card reports the size of its content in its own layout units: `width` 0 keeps the width the
        keeper chose, otherwise the view needs exactly that width. Unless the keeper has resized the card,
        its default height is half the screen's work area, so two stacked cards fill it, and the content is
        scaled to fit (zoom), width following from height."""
        self.last = (width, height, radius)
        self.radius = radius
        height = max(80, height)
        fixed = width > 0
        self.fixed_width = fixed
        layout_w = width if fixed else DEFAULT_WIDTH     # the card is laid out at this width and only scaled
        if self.dragging:
            return                          # KWin owns the geometry until release
        if not self.user:
            target = max(120, self.work_height() // len(CARDS))
            if fixed:
                zoom = max(MIN_ZOOM, min(MAX_ZOOM, target / height))
            else:
                # the cards share one zoom, so they are one width; the card with the tallest content fills
                # its half of the area and the others get the spare height below their content
                zoom = self.ctl.default_zoom()
            win_h = target
            win_w = round(layout_w * zoom)
        else:
            if fixed or self.fixed_was:     # gauges view <-> full: the stored zoom decides the width
                zoom = self.zoom
            else:
                zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.width() / layout_w))
            win_w = round(layout_w * zoom)
            win_h = round(height * zoom)
        if abs(zoom - self.zoom) > 1e-4:
            self.zoom = zoom
            self.bridge.zoomChanged.emit()
        if fixed:
            self.setMinimumWidth(win_w)
            self.setMaximumWidth(win_w)
        else:
            self.setMinimumWidth(MIN_PX)
            self.setMaximumWidth(MAX_PX)
        self.fixed_was = fixed
        if win_h != self.height() or not self.sized:
            self.setMinimumHeight(win_h)
            self.setMaximumHeight(win_h)
        if (win_w, win_h) != (self.width(), self.height()):
            self.resize(win_w, win_h)
        if DEBUG:
            print(f"[{self.key}] size layout={width}x{height} zoom={self.zoom:.3f} window={win_w}x{win_h} "
                  f"work={self.work_height()} user={self.user}", file=sys.stderr, flush=True)
        self.sized = True
        if self.wanted and not self.isVisible():
            self._reveal()
        self.update_blur()
        if not fixed:
            self.ctl.share_default_zoom(self)

    fixed_was = False

    def rederive(self):
        """The card moved to another screen: its default size follows that screen (not if the keeper resized it)."""
        if not self.user and self.last:
            self.content_size(*self.last)

    def begin_drag(self):
        self.dragging = True
        self.drag_idle.start(3000 if self.ctl.placer else 700)   # KWin reports the end; this is the backstop

    def end_drag(self):
        """The edge drag is over: keep the size as the keeper's, then give the card the height that fits it."""
        if not self.dragging:
            return
        self.dragging = False
        self.drag_idle.stop()
        self._save_geometry()
        # KWin still commits the last configure of the drag just after it reports the end; a height set now
        # would be overwritten by it, so give it a moment.
        QTimer.singleShot(300, self._after_drag)

    def default_width(self):
        """Window width of the cards at their default size, half the work area high (see content_size)."""
        return round(DEFAULT_WIDTH * self.ctl.default_zoom())

    def own_default_zoom(self):
        """The zoom at which this card's content is exactly half its work area high."""
        target = max(120, self.work_height() // len(CARDS))
        return max(MIN_ZOOM, min(MAX_ZOOM, target / max(80, self.last[1])))

    def _after_drag(self):
        if self.last and not self.fixed_width and not self.dragging:
            # Snap: a width released near the default size returns the card to it, exactly half the work
            # area high, so stacked cards line up. KWin owns the drag, so the snap happens on release.
            default_w = self.default_width()
            if abs(self.width() - default_w) <= max(SNAP_PX, round(default_w * SNAP_FRACTION)):
                self.user = False
                self.ctl.store.setValue(f"{self.key}/usersize", False)
                self.ctl.store.remove(f"{self.key}/width")
                self.ctl.store.remove(f"{self.key}/zoom")
                if DEBUG:
                    print(f"[{self.key}] snapped {self.width()} -> default {default_w}", file=sys.stderr, flush=True)
            else:
                self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.width() / DEFAULT_WIDTH))
                self.bridge.zoomChanged.emit()
            self.content_size(*self.last)
            self._save_geometry()

    def mark_user_sized(self):
        if not self.user:
            self.user = True
            self.ctl.store.setValue(f"{self.key}/usersize", True)
            self.ctl.store.setValue(f"{self.key}/zoom", self.zoom)

    def _resized(self, *_):
        if DEBUG:
            print(f"[{self.key}] geometry {self.width()}x{self.height()} dragging={self.dragging} user={self.user} "
                  f"(Qt x/y are placeholders on Wayland)", file=sys.stderr, flush=True)
        self.update_blur()
        if self.dragging:
            # the card scales with the window while the keeper drags: no reflow, no geometry of its own
            zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.width() / DEFAULT_WIDTH))
            if abs(zoom - self.zoom) > 1e-4:
                self.zoom = zoom
                self.bridge.zoomChanged.emit()
            self.drag_idle.start(3000 if self.ctl.placer else 700)
        elif self.isVisible():
            self.width_saved.start()

    def _moved(self, *_):
        if self.isVisible() and not self.ctl.wayland:
            self.width_saved.start()

    def _save_geometry(self):
        if self.dragging:
            return
        if self.user and not self.fixed_width:
            self.ctl.store.setValue(f"{self.key}/width", self.width())
            self.ctl.store.setValue(f"{self.key}/zoom", self.zoom)
        if not self.ctl.wayland and self.isVisible():
            self.ctl.store.setValue(f"{self.key}/pos", [self.x(), self.y()])

    def update_blur(self):
        if not self.isVisible():
            return
        rects = rounded_rects(self.width(), self.height(), self.radius * self.zoom)
        if self.ctl.wayland:
            wlblur.set_blur(self.win, rects)
        elif QGuiApplication.platformName() == "xcb":
            _blur_behind_x11(self.win, rects)

    def set_above(self, above):
        if self.ctl.wayland:
            return                         # KWin's script applies it
        visible = self.isVisible()
        self.setFlags(Gadget.flags_for(self.ctl))
        if visible:
            self.show()
            QTimer.singleShot(0, self.update_blur)


@ClassInfo({"D-Bus Interface": IFACE})
class Service(QObject):
    """org.pcmeter.PCmeter on the session bus: the single-instance handoff and KWin's move reports."""

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl

    @Slot()
    def show(self):
        self.ctl.show_all()

    @Slot(str, int, int, int, int)
    def workArea(self, name, x, y, w, h):
        self.ctl.set_work_area(name, QRect(x, y, w, h))

    @Slot(str, bool)
    def resizeState(self, window_caption, on):
        """KWin: an interactive resize of this card started (on) or finished."""
        for key, label in CARDS:
            if caption(label) == window_caption and not on:
                self.ctl.gadgets[key].end_drag()

    @Slot(str, result=int)
    def snapWidth(self, window_caption):
        """KWin, as an edge drag starts: the width this card's deadband holds (its default width), 0 for none."""
        for key, label in CARDS:
            gadget = self.ctl.gadgets.get(key)
            if caption(label) == window_caption and gadget and gadget.last and not gadget.fixed_width:
                width = gadget.default_width()
                if DEBUG:
                    print(f"[{key}] deadband target {width}", file=sys.stderr, flush=True)
                return width
        return 0

    @Slot(str, int, int, int, int)
    def frame(self, window_caption, left, top, right, bottom):
        if DEBUG:
            print(f"[kwin] {window_caption} frame left={left} top={top} right={right} bottom={bottom} "
                  f"({right - left}x{bottom - top})", file=sys.stderr, flush=True)

    @Slot(str, int, int)
    def moved(self, window_caption, x, y):
        self.ctl.window_moved(window_caption, x, y)


class Controller(QObject):
    def __init__(self, app, telemetry, bus):
        super().__init__(app)
        self.app, self.telemetry, self.bus = app, telemetry, bus
        self.feed = Feed(self)
        self.feed.history = getattr(telemetry, "history", None)
        telemetry.listeners.append(self.feed.push)
        self.engine = QQmlEngine(self)
        self.engine.quit.connect(self.quit)
        self.screensaver = self.logind = False     # the session is locked when either says so
        self.present = None
        self.wayland = QGuiApplication.platformName() == "wayland"
        self.store = QSettings(APP_ID, APP_ID)
        self.quitting = False
        self.skin = str(self.store.value("skin", "glass"))
        legacy = "compact" if self.store.value("compact", "false") in ("true", True) else "full"
        self.view = str(self.store.value("view", legacy))
        self.sound = self.store.value("sound", "true") in ("true", True)   # the master; cards start muted
        self.above = self.store.value("above", "false") in ("true", True)
        self.icon = _icon()

        self.service = Service(self)
        if bus.isConnected():
            bus.registerObject(OBJECT_PATH, self.service, QDBusConnection.RegisterOption.ExportAllSlots)

        self.areas = {}              # Wayland: screen name -> work area, reported by the KWin script
        self.placer = None
        if self.wayland and bus.isConnected() and kwin.present():
            self.placer = kwin.Placer(APP_ID, SERVICE, OBJECT_PATH, IFACE)
            self._load_placer()

        self._build_actions()
        self.tray = None
        self.tray_menu = self._menu(None)              # also the cards' menu (popup_menu)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setToolTip("PCmeter")
            self.tray.setContextMenu(self.tray_menu)
            self.tray.activated.connect(self._tray_clicked)
            self.tray.show()

        self.gadgets = {key: Gadget(self, key, label) for key, label in CARDS}
        self._watch_lock()
        if os.environ.get("PCMETER_SHOTS"):
            self._debug_shots(os.environ["PCMETER_SHOTS"])
        shown = [key for key, _ in CARDS if self.store.value(f"{key}/shown", "true") in ("true", True)]
        for key in shown or [key for key, _ in CARDS]:      # all dismissed last time: show them all
            self.set_card(key, True)
        app.aboutToQuit.connect(self._shutdown)

    # ---- settings
    def _set(self, name, value):
        setattr(self, name, value)
        self.store.setValue(name, value)
        for g in self.gadgets.values():
            getattr(g.bridge, f"{name}Changed").emit()
        if name == "view":
            self.update_wants()

    # ---- presence: sample and animate only while a card can be seen
    def _debug_shots(self, folder):
        """PCMETER_SHOTS=dir: save each card window (its own grabWindow, not the screen) after 12 s and every 60 s."""
        def shoot():
            for k, g in self.gadgets.items():
                if g.isVisible():
                    g.grabWindow().save(f"{folder}/host-{self.skin}-{self.view}-{k}.png")
        def burst(n=[0]):                     # 10 grabs 40 ms apart, to see that motion is continuous
            for k, g in self.gadgets.items():
                if g.isVisible():
                    g.grabWindow().save(f"{folder}/burst-{k}-{n[0]:02d}.png")
            n[0] += 1
            if n[0] < 10:
                QTimer.singleShot(40, burst)
        after = int(os.environ.get("PCMETER_SHOTS_AFTER", "12")) * 1000
        skins = [x for x in os.environ.get("PCMETER_SKINS", "").split(",") if x]
        orig = self.skin
        def per_skin(i=[0]):                  # PCMETER_SKINS=a,b,c: one grab of each card per skin, after the first delay
            if i[0] >= len(skins):
                self._set("skin", orig)       # put the keeper's skin back (it is stored)
                return
            self._set("skin", skins[i[0]])
            def grab(name=skins[i[0]]):
                for k, g in self.gadgets.items():
                    if g.isVisible():
                        g.grabWindow().save(f"{folder}/skin-{name}-{self.view}-{k}.png")
            QTimer.singleShot(2500, grab)
            i[0] += 1
            QTimer.singleShot(4000, per_skin)
        if skins:
            QTimer.singleShot(after + 4000, per_skin)
        QTimer.singleShot(after, shoot)
        QTimer.singleShot(after + 2000, burst)

    def update_wants(self):
        """Ask the sampler for just what the visible cards show: the process list in the full view, the
        readouts beside the dial in the full and compact views, the dial alone in gauges."""
        wants = {"procs": self.view == "full", "full": self.view != "gauge"}
        self.telemetry.want = {k: (dict(wants) if g.wanted else None) for k, g in self.gadgets.items()}

    def update_presence(self):
        """Evented: called when a card shows, hides or minimises and when the lock state changes."""
        if not hasattr(self, "gadgets") or self.quitting:
            return
        locked = self.screensaver or self.logind
        seen = {k: (not locked and g.isVisible() and g.visibility() != QWindow.Visibility.Minimized)
                for k, g in self.gadgets.items()}
        for k, g in self.gadgets.items():
            g.bridge.set_active(seen[k])
        present = any(seen.values())
        if DEBUG:
            print("pcmeter: presence locked=%s (screensaver=%s logind=%s) cards=%s" % (
                locked, self.screensaver, self.logind,
                {k: (g.isVisible(), g.visibility().name) for k, g in self.gadgets.items()}), file=sys.stderr, flush=True)
        if present != self.present:
            self.present = present
            self.telemetry.set_local(present)

    def _watch_lock(self):
        """Session lock, from KDE's screen locker (ScreenSaver.ActiveChanged) and from logind (LockedHint
        and the Lock/Unlock signals). Both are D-Bus signals: nothing polls."""
        if self.bus.isConnected():
            for path in ("/ScreenSaver", "/org/freedesktop/ScreenSaver"):
                self.bus.connect("org.freedesktop.ScreenSaver", path, "org.freedesktop.ScreenSaver",
                                 "ActiveChanged", self, "1screensaverChanged(bool)")
            reply = self.bus.call(QDBusMessage.createMethodCall(
                "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "GetActive"), timeout=500)
            if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
                self.screensaver = bool(reply.arguments()[0])
        system = QDBusConnection.systemBus()
        if not system.isConnected():
            return
        manager = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")
        # A service started by systemd --user is outside the logind session, so GetSessionByPID can fail:
        # use the session id from the environment first. If nothing is found we cannot know, so the cards stay
        # "watched" (never "locked"); KDE's ScreenSaver signal still covers a lock.
        def call(*args, **kw):
            m = QDBusMessage.createMethodCall(*args)
            m.setArguments(kw.get("args", []))
            r = system.call(m, timeout=500)
            return r.arguments() if r.type() == QDBusMessage.MessageType.ReplyMessage and r.arguments() else None
        sid = os.environ.get("XDG_SESSION_ID")
        found = call(*manager[:2], manager[2], "GetSession", args=[sid]) if sid else None
        found = found or call(*manager[:2], manager[2], "GetSessionByPID", args=[os.getpid()])
        if not found:
            return
        self.session_path = _path(found[0])
        system.connect("org.freedesktop.login1", self.session_path, "org.freedesktop.DBus.Properties",
                       "PropertiesChanged", self, "1logindChanged()")
        system.connect("org.freedesktop.login1", self.session_path, "org.freedesktop.login1.Session",
                       "Lock", self, "1logindLock()")
        system.connect("org.freedesktop.login1", self.session_path, "org.freedesktop.login1.Session",
                       "Unlock", self, "1logindUnlock()")
        self.logindChanged()

    @Slot(bool)
    def screensaverChanged(self, active):
        self.screensaver = bool(active)
        self.update_presence()

    @Slot()
    def logindLock(self):
        self.logind = True
        self.update_presence()

    @Slot()
    def logindUnlock(self):
        self.logind = False
        self.update_presence()

    @Slot()
    def logindChanged(self):
        msg = QDBusMessage.createMethodCall("org.freedesktop.login1", self.session_path,
                                            "org.freedesktop.DBus.Properties", "Get")
        msg.setArguments(["org.freedesktop.login1.Session", "LockedHint"])
        reply = QDBusConnection.systemBus().call(msg, timeout=500)
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
            self.logind = _unwrap(reply.arguments()[0]) is True
            self.update_presence()

    def _load_placer(self):
        saved = {}
        for key, label in CARDS:
            pos = self.store.value(f"{key}/wlpos")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                saved[caption(label)] = (int(pos[0]), int(pos[1]))
        order = [caption(label) for _, label in reversed(CARDS)]   # right to left: GPU, then CPU
        self.placer.load(order, saved, self.above, DEBUG, (SNAP_PX, SNAP_FRACTION))

    def window_moved(self, window_caption, x, y):
        for key, label in CARDS:
            if caption(label) == window_caption:
                self.store.setValue(f"{key}/wlpos", [x, y])

    def default_zoom(self):
        """The zoom the full-width cards share at their default size: the largest at which every card's content
        fits in half its work area, so the cards are one width and each exactly half the area high."""
        zooms = [g.own_default_zoom() for g in getattr(self, "gadgets", {}).values() if g.last and not g.fixed_width]
        return min(zooms) if zooms else 1.0

    def share_default_zoom(self, source):
        """A card's content changed size: the other default-sized cards follow the shared width."""
        zoom = self.default_zoom()
        for g in self.gadgets.values():
            if g is not source and not g.user and g.last and not g.fixed_width and not g.dragging \
                    and abs(g.zoom - zoom) > 1e-4:
                g.content_size(*g.last)

    def work_area(self, screen):
        """The screen minus panels. Qt knows it on X11; on Wayland KWin's script reports it."""
        return self.areas.get(screen.name()) or screen.availableGeometry()

    def set_work_area(self, name, rect):
        if DEBUG:
            print(f"work area {name}: {rect.x()},{rect.y()} {rect.width()}x{rect.height()}", file=sys.stderr, flush=True)
        if self.areas.get(name) != rect:
            self.areas[name] = rect
            for g in getattr(self, "gadgets", {}).values():
                g.rederive()

    def default_position(self, gadget):
        """X11: the cards stack flush in one column at the right edge of the work area, CPU on top."""
        screen = self.work_area(gadget.win.screen() or QGuiApplication.primaryScreen())
        i = [k for k, _ in CARDS].index(gadget.key)
        return QPoint(screen.right() + 1 - gadget.width(), screen.top() + i * (screen.height() // len(CARDS)))

    # ---- cards
    def set_card(self, key, on):
        g = self.gadgets[key]
        self.store.setValue(f"{key}/shown", on)
        self.card_actions[key].setChecked(on)
        if on:
            g.open()
            self.update_wants()
            if g.isVisible():
                g.raise_()
                g.requestActivate()
        else:
            g.dismiss()
            self.update_wants()
            self.update_presence()
            if self.tray is None and not any(x.wanted for x in self.gadgets.values()):
                self.quit()

    def show_all(self):
        for key, _ in CARDS:
            self.set_card(key, True)

    # ---- menu
    def _build_actions(self):
        self.card_actions = {}
        for key, label in CARDS:
            a = QAction(f"Show {label}", self, checkable=True)
            a.triggered.connect(lambda on, k=key: self.set_card(k, on))
            self.card_actions[key] = a
        self.skin_group = QActionGroup(self)
        self.skin_actions = []
        for value, label in SKINS:
            a = QAction(label, self, checkable=True)
            a.setChecked(value == self.skin)
            a.triggered.connect(lambda _=False, v=value: self._set("skin", v))
            self.skin_group.addAction(a)
            self.skin_actions.append(a)
        self.view_group = QActionGroup(self)
        self.view_actions = []
        for value, label in VIEWS:
            a = QAction(label, self, checkable=True)
            a.setChecked(value == self.view)
            a.triggered.connect(lambda _=False, v=value: self._set("view", v))
            self.view_group.addAction(a)
            self.view_actions.append(a)
        self.sound_action = QAction("Clicks", self, checkable=True, checked=self.sound)
        self.sound_action.toggled.connect(lambda on: self._set("sound", on))
        self.above_action = QAction("Keep on top", self, checkable=True, checked=self.above)
        self.above_action.toggled.connect(self._set_above)
        self.autostart_action = QAction("Launch on startup", self, checkable=True, checked=autostart_enabled())
        self.autostart_action.toggled.connect(self._set_autostart)
        self.quit_action = QAction("Quit PCmeter", self)
        self.quit_action.triggered.connect(self.quit)

    def _menu(self, parent):
        menu = QMenu(parent)
        for key, _ in CARDS:
            menu.addAction(self.card_actions[key])
        menu.addSeparator()
        skins = menu.addMenu("Skin")
        for a in self.skin_actions:
            skins.addAction(a)
        views = menu.addMenu("View")
        for a in self.view_actions:
            views.addAction(a)
        menu.addAction(self.sound_action)
        menu.addAction(self.above_action)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        # the autostart entry can change outside PCmeter (system settings), so read it each time
        menu.aboutToShow.connect(self._sync_autostart)
        return menu

    def _sync_autostart(self):
        on = autostart_enabled()
        if self.autostart_action.isChecked() != on:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(on)
            self.autostart_action.blockSignals(False)

    def _set_autostart(self, on):
        try:
            set_autostart(on)
        except OSError as e:
            print(f"pcmeter: launch on startup: {e}", file=sys.stderr, flush=True)
        self._sync_autostart()

    def popup_menu(self, gadget, point):
        """A card's menu button or right-click: the tray's own menu, so both are one menu. On Wayland a window
        does not know where it is, so the menu is tied to the card's window and placed relative to it."""
        menu = getattr(self, "tray_menu", None) or self._menu(None)
        if menu.isVisible():
            menu.hide()
        menu.winId()                                   # make the native window so it can take a transient parent
        if menu.windowHandle():
            menu.windowHandle().setTransientParent(gadget.win)
        menu.popup(gadget.mapToGlobal(point))

    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_all()

    def _set_above(self, on):
        self.above = on
        self.store.setValue("above", on)
        if self.placer:
            self._load_placer()
        for g in self.gadgets.values():
            g.set_above(on)

    # ---- quitting
    def quit(self):
        self.quitting = True
        self.app.quit()

    def _shutdown(self):
        self.quitting = True
        self.telemetry.set_local(False)
        for g in self.gadgets.values():
            if g.isVisible():
                g._save_geometry()
        if self.placer:
            self.placer.unload()
        self.store.sync()


def _blur_behind_x11(window, rects):
    """Ask KWin (X11 or XWayland) to blur behind the window, inside `rects` (device px)."""
    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        return
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return
    try:
        dpr = window.devicePixelRatio()
        flat = [int(round(v * dpr)) for rect in rects for v in rect]
        data = (ctypes.c_long * max(1, len(flat)))(*flat)
        atom = x11.XInternAtom(display, b"_KDE_NET_WM_BLUR_BEHIND_REGION", 0)
        cardinal = x11.XInternAtom(display, b"CARDINAL", 0)
        x11.XChangeProperty(display, int(window.winId()), atom, cardinal, 32, 0, data, len(flat))
        x11.XFlush(display)
    finally:
        x11.XCloseDisplay(display)


def _icon():
    for base in (getattr(sys, "_MEIPASS", None), Path(__file__).resolve().parent.parent / "packaging"):
        if base and (Path(base) / "pcmeter.svg").exists():
            return QIcon(str(Path(base) / "pcmeter.svg"))
    return QIcon.fromTheme("utilities-system-monitor")


def _icon():
    for base in (getattr(sys, "_MEIPASS", None), Path(__file__).resolve().parent.parent / "packaging"):
        if base and (Path(base) / "pcmeter.svg").exists():
            return QIcon(str(Path(base) / "pcmeter.svg"))
    return QIcon.fromTheme("utilities-system-monitor")



def run(start_sampler):
    """Show the gadgets and block until PCmeter quits. `start_sampler()` starts telemetry (paused until a
    card is visible) and returns it. Returns the process exit code; 0 at once when another copy is
    running (that copy re-shows its cards)."""
    if DEBUG:
        import faulthandler, signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)     # kill -USR1 <pid>: dump every Python stack
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if RENDER == "cpu":
        os.environ["QT_QUICK_BACKEND"] = "software"
        # Qt's own GL integrations open the GPU at start-up even when nothing draws with GL (a small
        # context shows up in nvidia-smi). Without them, windows are plain shared-memory buffers.
        os.environ["QT_WAYLAND_CLIENT_BUFFER_INTEGRATION"] = "none"
        os.environ["QT_XCB_GL_INTEGRATION"] = "none"
    if RENDER != "cpu":
        # set here, before Qt opens any GL context, and only where the user has not set them
        os.environ.setdefault("__GL_YIELD", "USLEEP")
        os.environ.setdefault("QSG_RENDER_LOOP", "threaded" if RENDER == "threaded" else "basic")
        if RENDER == "vulkan":
            os.environ.setdefault("QSG_RHI_BACKEND", "vulkan")
    QApplication.setApplicationName("PCmeter")
    QApplication.setOrganizationName("PCmeter")
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    bus = QDBusConnection.sessionBus()
    if bus.isConnected() and not bus.registerService(SERVICE):
        bus.call(QDBusMessage.createMethodCall(SERVICE, OBJECT_PATH, IFACE, "show"))
        return 0
    telemetry = start_sampler()
    ctl = Controller(app, telemetry, bus)   # noqa: F841 (lives as long as the app)
    return app.exec()
