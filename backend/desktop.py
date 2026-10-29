"""PCmeter desktop gadgets: the CPU and GPU cards as separate frameless, translucent windows.

There is no outer window. Each card is its own window, exactly the size of the card, and the
desktop shows through it. Drag a card anywhere to move it, drag its left or right edge to change
its width (the height follows the content), and use its buttons to dismiss it or open the menu.
The menu (also in the system tray) picks the skin, the view (full, compact, gauges), clicks, keep-on-top, and which
cards are shown.

Wayland (KDE Plasma): windows stay native so they are sharp at fractional scales. KWin does
the placing (see kwin.py) and the blur behind each card (see wlblur.py).
X11: the windows place themselves and ask KWin for blur with _KDE_NET_WM_BLUR_BEHIND_REGION.

One copy runs per user: launching again re-shows any dismissed card (D-Bus org.pcmeter.PCmeter).
"""
import ctypes
import json
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import ClassInfo, QFile, QIODevice, QObject, QPoint, QSettings, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QGuiApplication, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import kwin
import wlblur

APP_ID = "pcmeter"
SERVICE, OBJECT_PATH, IFACE = "org.pcmeter.PCmeter", "/PCmeter", "org.pcmeter.PCmeter"
CARDS = (("cpu", "CPU"), ("gpu", "GPU"))
SKINS = (("glass", "Glass"), ("matrix", "Matrix"), ("hive", "Hive"), ("windows", "Windows"),
         ("mac", "macOS"), ("linux", "Linux"), ("dark", "Dark"), ("white", "White"))
VIEWS = (("full", "Full"), ("compact", "Compact"), ("gauge", "Gauges only"))
DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH = 440, 340, 900
DEBUG = bool(os.environ.get("PCMETER_DEBUG"))
RENDER = os.environ.get("PCMETER_RENDER", "cpu")           # "gpu" to let Chromium use the GPU
PAGE_ARGS = os.environ.get("PCMETER_PAGE_ARGS", "")       # extra query for the gadget pages (testing)

BRIDGE_JS = """
(() => {
  %(qwebchannel)s
  let resolve;
  window.pcmeterHost = new Promise(r => { resolve = r; });
  new QWebChannel(qt.webChannelTransport, ch => resolve(ch.objects.host));
})();
"""


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


class Bridge(QObject):
    """What a gadget page may ask of its window. Edges use Qt.Edge bits: left 1, right 2."""
    settingsChanged = Signal(str)

    def __init__(self, gadget):
        super().__init__(gadget)
        self.gadget = gadget

    @Slot(result=str)
    def settings(self):
        return self.gadget.ctl.settings_json()

    @Slot()
    def startMove(self):
        handle = self.gadget.windowHandle()
        if handle:
            handle.startSystemMove()

    @Slot(int)
    def startResize(self, edges):
        handle = self.gadget.windowHandle()
        edges &= int(Qt.Edge.LeftEdge.value | Qt.Edge.RightEdge.value)
        if handle and edges:
            handle.startSystemResize(Qt.Edges(edges))

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


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        if DEBUG:
            print(f"[{self.objectName()}] {message}", flush=True)


class Gadget(QWidget):
    def __init__(self, ctl, key, label, url, profile):
        super().__init__(None, Gadget.flags_for(ctl))
        self.ctl, self.key, self.label = ctl, key, label
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(caption(label))
        self.setWindowIcon(ctl.icon)
        self.radius = 0.0
        self.fixed_width = False
        self.wanted = False          # should be on screen once the page has its size
        self.sized = False
        self.width_saved = QTimer(self, singleShot=True, interval=500, timeout=self._save_geometry)

        width = int(ctl.store.value(f"{key}/width", DEFAULT_WIDTH))
        width = max(MIN_WIDTH, min(MAX_WIDTH, width))
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)
        self.resize(width, 620)

        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent")
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setGeometry(0, 0, width, 620)
        page = Page(profile, self.view)
        page.setObjectName(key)
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.setPage(page)

        self.bridge = Bridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("host", self.bridge)
        page.setWebChannel(self.channel)
        script = QWebEngineScript()
        script.setName("pcmeter-bridge")
        script.setSourceCode(BRIDGE_JS % {"qwebchannel": ctl.qwebchannel_js})
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        page.scripts().insert(script)

        query = f"gadget={key}" + ("&lite" if RENDER == "cpu" else "") + ("&fps" if DEBUG else "") + (f"&{PAGE_ARGS}" if PAGE_ARGS else "")
        self.view.load(QUrl(f"{url}?{query}"))
        self.reveal = QTimer(self, singleShot=True, interval=2500, timeout=self._reveal)

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
            self.reveal.start()              # never wait forever for the page to report its size

    def _reveal(self):
        if not self.wanted or self.isVisible():
            return
        if not self.ctl.wayland:
            pos = self.ctl.store.value(f"{self.key}/pos")
            if pos is None:
                pos = self.ctl.default_position(self)
            self.move(QPoint(int(pos[0]), int(pos[1])) if isinstance(pos, (list, tuple)) else pos)
        self.show()
        QTimer.singleShot(0, self.update_blur)
        QTimer.singleShot(250, self.update_blur)

    def dismiss(self):
        self.wanted = False
        if self.isVisible():
            self._save_geometry()
            if self.ctl.wayland and self.windowHandle():
                wlblur.clear_blur(self.windowHandle())
            self.hide()

    def closeEvent(self, event):         # Alt+F4 and the like dismiss the card
        if not self.ctl.quitting:
            event.ignore()
            self.ctl.set_card(self.key, False)
            return
        super().closeEvent(event)

    # ---- size, shape and blur
    def content_size(self, width, height, radius):
        self.radius = radius
        height = max(80, height)
        if width > 0:                       # gauges view: exactly the dial's width
            self.fixed_width = True
            self.setFixedWidth(width)
        elif self.fixed_width:              # back to the user's width
            self.fixed_width = False
            self.setMinimumWidth(MIN_WIDTH)
            self.setMaximumWidth(MAX_WIDTH)
            self.resize(int(self.ctl.store.value(f"{self.key}/width", DEFAULT_WIDTH)), self.height())
        if height != self.height() or not self.sized:
            self.setFixedHeight(height)
        self.sized = True
        if self.wanted and not self.isVisible():
            self._reveal()
        self.update_blur()

    def resizeEvent(self, event):
        self.view.setGeometry(self.rect())
        self.update_blur()
        if self.isVisible():
            self.width_saved.start()
        super().resizeEvent(event)

    def moveEvent(self, event):
        if self.isVisible() and not self.ctl.wayland:
            self.width_saved.start()
        super().moveEvent(event)

    def _save_geometry(self):
        if not self.fixed_width:
            self.ctl.store.setValue(f"{self.key}/width", self.width())
        if not self.ctl.wayland and self.isVisible():
            self.ctl.store.setValue(f"{self.key}/pos", [self.x(), self.y()])

    def update_blur(self):
        if not self.isVisible() or not self.windowHandle():
            return
        rects = rounded_rects(self.width(), self.height(), self.radius)
        if self.ctl.wayland:
            wlblur.set_blur(self.windowHandle(), rects)
        else:
            _blur_behind_x11(self, rects)

    def set_above(self, above):
        if self.ctl.wayland:
            return                         # KWin's script applies it
        visible = self.isVisible()
        self.setWindowFlags(Gadget.flags_for(self.ctl))
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
    def __init__(self, app, url, bus):
        super().__init__(app)
        self.app, self.url, self.bus = app, url, bus
        self.wayland = QGuiApplication.platformName() == "wayland"
        self.store = QSettings(APP_ID, APP_ID)
        self.quitting = False
        self.skin = str(self.store.value("skin", "glass"))
        legacy = "compact" if self.store.value("compact", "false") in ("true", True) else "full"
        self.view = str(self.store.value("view", legacy))
        self.sound = self.store.value("sound", "true") in ("true", True)   # the master; cards start muted
        self.above = self.store.value("above", "false") in ("true", True)
        self.icon = _icon()
        self.qwebchannel_js = _qwebchannel_js()

        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "pcmeter"
        self.profile = QWebEngineProfile("pcmeter", self)
        self.profile.setPersistentStoragePath(str(data / "web"))
        self.profile.setCachePath(str(data / "web-cache"))
        self.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        self.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)

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

        self.gadgets = {key: Gadget(self, key, label, url, self.profile) for key, label in CARDS}
        shown = [key for key, _ in CARDS if self.store.value(f"{key}/shown", "true") in ("true", True)]
        for key in shown or [key for key, _ in CARDS]:      # all dismissed last time: show them all
            self.set_card(key, True)
        app.aboutToQuit.connect(self._shutdown)

    # ---- settings
    def settings_json(self):
        return json.dumps({"skin": self.skin, "view": self.view, "sound": self.sound})

    def _push(self):
        payload = self.settings_json()
        for g in self.gadgets.values():
            g.bridge.settingsChanged.emit(payload)

    def _set(self, name, value):
        setattr(self, name, value)
        self.store.setValue(name, value)
        self._push()

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
            if g.isVisible():
                g.raise_()
                g.activateWindow()
        else:
            g.dismiss()
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
        menu = self._menu(gadget)
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
        for g in self.gadgets.values():
            if g.isVisible():
                g._save_geometry()
        if self.placer:
            self.placer.unload()
        self.store.sync()


def _qwebchannel_js():
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("qwebchannel.js is missing from this Qt build")
    try:
        return bytes(f.readAll().data()).decode()
    finally:
        f.close()


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
        dpr = window.devicePixelRatioF()
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


def _gpu_info(app):
    """PCMETER_GPU_INFO=1: print what Chromium's GPU stack reports (chrome://gpu) and exit."""
    view = QWebEngineView()
    view.resize(900, 700)

    def done(ok):
        QTimer.singleShot(1500, lambda: view.page().toPlainText(lambda text: (print(text, flush=True), app.quit())))

    view.loadFinished.connect(done)
    view.load(QUrl("chrome://gpu"))
    view.show()
    return app.exec()


def run(start_server):
    """Show the gadgets and block until PCmeter quits. `start_server()` starts telemetry and returns its URL.
    Returns the process exit code; 0 at once when another copy is running (that copy re-shows its cards)."""
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if RENDER == "cpu":
        # Draw on the CPU only. A monitor should not wake the GPU it is watching: Chromium rasters
        # and composites in software, and Qt Quick (under the web view) uses its software backend.
        os.environ["QT_QUICK_BACKEND"] = "software"
        # Qt's own GL integrations open the GPU at start-up even when nothing draws with GL (a small
        # context shows up in nvidia-smi). Without them, windows are plain shared-memory buffers.
        os.environ["QT_WAYLAND_CLIENT_BUFFER_INTEGRATION"] = "none"
        os.environ["QT_XCB_GL_INTEGRATION"] = "none"
        flags = "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')} {flags}".strip()
    else:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QApplication.setApplicationName("PCmeter")
    QApplication.setOrganizationName("PCmeter")
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if os.environ.get("PCMETER_GPU_INFO"):
        return _gpu_info(app)

    bus = QDBusConnection.sessionBus()
    if bus.isConnected() and not bus.registerService(SERVICE):
        bus.call(QDBusMessage.createMethodCall(SERVICE, OBJECT_PATH, IFACE, "show"))
        return 0
    url = start_server()
    ctl = Controller(app, url, bus)   # noqa: F841 (lives as long as the app)
    return app.exec()
