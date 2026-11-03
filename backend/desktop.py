"""PCmeter desktop gadgets: the CPU and GPU cards as separate frameless, translucent windows.

There is no outer window. Each card is its own Qt Quick window (qml/Card.qml), exactly the size of
the card, and the desktop shows through it. Drag a card anywhere to move it, drag its left or right
edge to change its width (the height follows the content), and use its buttons to dismiss it or open
the menu. The menu (also in the system tray) picks the skin, the view (full, compact, gauges), clicks,
keep-on-top, and which cards are shown.

The cards cost nothing while nothing moves: the scene graph renders only when an animation runs, and
sampling stops entirely (the sampler waits on an event) while every card is hidden or minimised or
the session is locked. The telemetry reaches the windows in-process, with no server.

Wayland (KDE Plasma): windows stay native so they are sharp at fractional scales. KWin does
the placing (see kwin.py) and the blur behind each card (see wlblur.py).
X11: the windows place themselves and ask KWin for blur with _KDE_NET_WM_BLUR_BEHIND_REGION.

One copy runs per user: launching again re-shows any dismissed card (D-Bus org.pcmeter.PCmeter).
"""
import ctypes
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import ClassInfo, Property, QEvent, QObject, QPoint, QSettings, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QSurfaceFormat, QWindow
from PySide6.QtQml import QQmlEngine
from PySide6.QtQuick import QQuickView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import kwin
import wlblur

APP_ID = "pcmeter"
SERVICE, OBJECT_PATH, IFACE = "org.pcmeter.PCmeter", "/PCmeter", "org.pcmeter.PCmeter"
CARDS = (("cpu", "CPU"), ("gpu", "GPU"))
SKINS = (("glass", "Glass"), ("matrix", "Matrix"), ("hive", "Hive"), ("windows", "Windows"),
         ("mac", "macOS"), ("linux", "Linux"), ("dark", "Dark"), ("white", "White"))
VIEWS = (("full", "Full"), ("compact", "Compact"), ("gauge", "Gauges only"))
DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH = 440, 340, 900
# The cards are drawn by Qt Quick. "cpu" (default) uses its software renderer: a monitor should not
# wake the GPU it is watching (an NVIDIA context shows up in nvidia-smi and holds the card out of its
# idle clocks). "gpu" uses Qt's default hardware renderer (RHI: OpenGL or Vulkan).
RENDER = os.environ.get("PCMETER_RENDER", "cpu")
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


class Feed(QObject):
    """The latest telemetry snapshot, as a property the cards bind to. `push` is called on the sampler thread."""
    snapshotChanged = Signal()
    _arrive = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snap = {}
        self._arrive.connect(self._store)          # queued: delivered on the Qt thread

    def push(self, snap):
        self._arrive.emit(snap)

    @Slot(object)
    def _store(self, snap):
        self._snap = snap
        self.snapshotChanged.emit()

    snapshot = Property("QVariant", lambda self: self._snap, notify=snapshotChanged)


class Bridge(QObject):
    """What a card may ask of its window, and the settings it shows. Edges use Qt.Edge bits: left 1, right 2."""
    skinChanged, viewChanged, soundChanged = Signal(), Signal(), Signal()
    activeChanged, mutedChanged, volumeChanged, foldChanged = Signal(), Signal(), Signal(), Signal()

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
        edges &= int(Qt.Edge.LeftEdge.value | Qt.Edge.RightEdge.value)
        if edges:
            self.gadget.startSystemResize(Qt.Edges(edges))

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


class Gadget(QQuickView):
    def __init__(self, ctl, key, label):
        super().__init__(ctl.engine, None)
        self.ctl, self.key, self.label = ctl, key, label
        self.setFlags(Gadget.flags_for(ctl))
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)
        self.setColor(QColor(0, 0, 0, 0))
        self.setTitle(caption(label))
        self.setIcon(ctl.icon)
        self.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self.radius = 0.0
        self.fixed_width = False
        self.wanted = False          # should be on screen once the card has its size
        self.sized = False
        self.width_saved = QTimer(self, singleShot=True, interval=500, timeout=self._save_geometry)

        width = int(ctl.store.value(f"{key}/width", DEFAULT_WIDTH))
        width = max(MIN_WIDTH, min(MAX_WIDTH, width))
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)
        self.resize(width, 620)

        self.bridge = Bridge(self)
        self.setInitialProperties({"cardKey": key, "host": self.bridge, "feed": ctl.feed})
        self.setSource(QUrl.fromLocalFile(str(QML_DIR / "Card.qml")))
        for error in self.errors():
            print(f"[{key}] {error.toString()}", file=sys.stderr, flush=True)
        self.reveal = QTimer(self, singleShot=True, interval=2500, timeout=self._reveal)
        self.visibleChanged.connect(lambda _: ctl.update_presence())
        self.visibilityChanged.connect(lambda _: ctl.update_presence())

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
                wlblur.clear_blur(self)
            self.hide()

    def event(self, e):                      # Alt+F4 and the like dismiss the card
        if e.type() == QEvent.Type.Close and not self.ctl.quitting:
            e.ignore()
            self.ctl.set_card(self.key, False)
            return True
        return super().event(e)

    # ---- size, shape and blur
    def content_size(self, width, height, radius):
        self.radius = radius
        height = max(80, height)
        if width > 0:                       # gauges view: exactly the dial's width
            self.fixed_width = True
            self.setMinimumWidth(width)
            self.setMaximumWidth(width)
            if self.width() != width:
                self.resize(width, self.height())
        elif self.fixed_width:              # back to the user's width
            self.fixed_width = False
            self.setMinimumWidth(MIN_WIDTH)
            self.setMaximumWidth(MAX_WIDTH)
            self.resize(int(self.ctl.store.value(f"{self.key}/width", DEFAULT_WIDTH)), self.height())
        if height != self.height() or not self.sized:
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.resize(self.width(), height)
        self.sized = True
        if self.wanted and not self.isVisible():
            self._reveal()
        self.update_blur()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_blur()
        if self.isVisible():
            self.width_saved.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible() and not self.ctl.wayland:
            self.width_saved.start()

    def _save_geometry(self):
        if not self.fixed_width:
            self.ctl.store.setValue(f"{self.key}/width", self.width())
        if not self.ctl.wayland and self.isVisible():
            self.ctl.store.setValue(f"{self.key}/pos", [self.x(), self.y()])

    def update_blur(self):
        if not self.isVisible():
            return
        rects = rounded_rects(self.width(), self.height(), self.radius)
        if self.ctl.wayland:
            wlblur.set_blur(self, rects)
        elif QGuiApplication.platformName() == "xcb":
            _blur_behind_x11(self, rects)

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

    @Slot(str, int, int)
    def moved(self, window_caption, x, y):
        self.ctl.window_moved(window_caption, x, y)


class Controller(QObject):
    def __init__(self, app, telemetry, bus):
        super().__init__(app)
        self.app, self.telemetry, self.bus = app, telemetry, bus
        self.feed = Feed(self)
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

        self.placer = None
        if self.wayland and bus.isConnected() and kwin.present():
            self.placer = kwin.Placer(APP_ID, SERVICE, OBJECT_PATH, IFACE)
            self._load_placer()

        self._build_actions()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setToolTip("PCmeter")
            self.tray_menu = self._menu(None)
            self.tray.setContextMenu(self.tray_menu)
            self.tray.activated.connect(self._tray_clicked)
            self.tray.show()

        self.gadgets = {key: Gadget(self, key, label) for key, label in CARDS}
        self._watch_lock()
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
        sid = os.environ.get("XDG_SESSION_ID")
        msg = QDBusMessage.createMethodCall(*manager, "GetSession" if sid else "GetSessionByPID")
        msg.setArguments([sid] if sid else [os.getpid()])
        reply = system.call(msg, timeout=500)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return
        self.session_path = str(reply.arguments()[0].path()) if hasattr(reply.arguments()[0], "path") else str(reply.arguments()[0])
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
            value = reply.arguments()[0]
            self.logind = bool(value.value() if hasattr(value, "value") else value)
            self.update_presence()

    def _load_placer(self):
        saved = {}
        for key, label in CARDS:
            pos = self.store.value(f"{key}/wlpos")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                saved[caption(label)] = (int(pos[0]), int(pos[1]))
        order = [caption(label) for _, label in reversed(CARDS)]   # right to left: GPU, then CPU
        self.placer.load(order, saved, self.above)

    def window_moved(self, window_caption, x, y):
        for key, label in CARDS:
            if caption(label) == window_caption:
                self.store.setValue(f"{key}/wlpos", [x, y])

    def default_position(self, gadget):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        i = [k for k, _ in reversed(CARDS)].index(gadget.key)
        return QPoint(screen.right() - 24 - (i + 1) * gadget.width() - i * 16, screen.top() + 24)

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
        menu.addSeparator()
        menu.addAction(self.quit_action)
        return menu

    def popup_menu(self, gadget, point):
        menu = self._menu(None)
        menu.aboutToHide.connect(menu.deleteLater)
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
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if RENDER == "cpu":
        os.environ["QT_QUICK_BACKEND"] = "software"
        # Qt's own GL integrations open the GPU at start-up even when nothing draws with GL (a small
        # context shows up in nvidia-smi). Without them, windows are plain shared-memory buffers.
        os.environ["QT_WAYLAND_CLIENT_BUFFER_INTEGRATION"] = "none"
        os.environ["QT_XCB_GL_INTEGRATION"] = "none"
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
