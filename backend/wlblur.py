"""Blur behind a Qt window on KDE Plasma (Wayland), via KWin's org_kde_kwin_blur protocol.

Qt has no public call for this, and KWindowSystem (which has one) would bring a second copy of Qt
into the process. So this speaks the protocol directly with libwayland-client over ctypes, on
Qt's own wl_display and the window's own wl_surface, using a private event queue so Qt's
dispatching is never disturbed. Every function returns quietly when anything is missing: blur is
an effect, never a requirement.
"""
import ctypes
from ctypes import POINTER, Structure, Union, c_char_p, c_int, c_int32, c_uint32, c_void_p

import shiboken6
from PySide6.QtCore import QByteArray


class _Message(Structure):
    _fields_ = [("name", c_char_p), ("signature", c_char_p), ("types", POINTER(c_void_p))]


class _Interface(Structure):
    _fields_ = [("name", c_char_p), ("version", c_int),
                ("method_count", c_int), ("methods", POINTER(_Message)),
                ("event_count", c_int), ("events", POINTER(_Message))]


class _Arg(Union):
    _fields_ = [("i", c_int32), ("u", c_uint32), ("s", c_char_p), ("o", c_void_p)]


_GLOBAL = ctypes.CFUNCTYPE(None, c_void_p, c_void_p, c_uint32, c_char_p, c_uint32)
_REMOVE = ctypes.CFUNCTYPE(None, c_void_p, c_void_p, c_uint32)


class _RegistryListener(Structure):
    _fields_ = [("global_", _GLOBAL), ("global_remove", _REMOVE)]


_DESTROY = 1          # WL_MARSHAL_FLAG_DESTROY
_state = None         # set up once, on first use


class _Wayland:
    def __init__(self, display):
        wl = ctypes.CDLL("libwayland-client.so.0")
        self.wl, self.display = wl, display
        wl.wl_display_create_queue.restype = c_void_p
        wl.wl_display_create_queue.argtypes = [c_void_p]
        wl.wl_proxy_create_wrapper.restype = c_void_p
        wl.wl_proxy_create_wrapper.argtypes = [c_void_p]
        wl.wl_proxy_wrapper_destroy.argtypes = [c_void_p]
        wl.wl_proxy_set_queue.argtypes = [c_void_p, c_void_p]
        wl.wl_proxy_get_version.restype = c_uint32
        wl.wl_proxy_get_version.argtypes = [c_void_p]
        wl.wl_proxy_add_listener.argtypes = [c_void_p, c_void_p, c_void_p]
        wl.wl_proxy_marshal_array_flags.restype = c_void_p
        wl.wl_proxy_marshal_array_flags.argtypes = [c_void_p, c_uint32, c_void_p, c_uint32, c_uint32, c_void_p]
        wl.wl_display_roundtrip_queue.argtypes = [c_void_p, c_void_p]
        wl.wl_display_flush.argtypes = [c_void_p]

        iface = lambda name: ctypes.addressof(_Interface.in_dll(wl, name))
        self.registry_iface = iface("wl_registry_interface")
        self.compositor_iface = iface("wl_compositor_interface")
        self.region_iface = iface("wl_region_interface")
        surface_iface = iface("wl_surface_interface")

        # org_kde_kwin_blur (requests: commit, set_region(?o), release) and its manager
        # (requests: create(n, o), unset(o)), as wayland-scanner would emit them.
        self._keep = []
        def types(*ptrs):
            arr = (c_void_p * max(1, len(ptrs)))(*ptrs)
            self._keep.append(arr)
            return ctypes.cast(arr, POINTER(c_void_p))
        def messages(*items):
            arr = (_Message * len(items))(*[_Message(n, s, t) for n, s, t in items])
            self._keep.append(arr)
            return arr
        self.blur_iface_obj = _Interface(b"org_kde_kwin_blur", 1, 3, None, 0, None)
        self.manager_iface_obj = _Interface(b"org_kde_kwin_blur_manager", 1, 2, None, 0, None)
        self.blur_iface = ctypes.addressof(self.blur_iface_obj)
        self.manager_iface = ctypes.addressof(self.manager_iface_obj)
        self.blur_iface_obj.methods = messages(
            (b"commit", b"", types()),
            (b"set_region", b"?o", types(self.region_iface)),
            (b"release", b"", types()))
        self.manager_iface_obj.methods = messages(
            (b"create", b"no", types(self.blur_iface, surface_iface)),
            (b"unset", b"o", types(surface_iface)))

        self.queue = wl.wl_display_create_queue(display)
        wrapper = wl.wl_proxy_create_wrapper(display)
        wl.wl_proxy_set_queue(wrapper, self.queue)
        self.registry = self._marshal(wrapper, 1, self.registry_iface, wl.wl_proxy_get_version(wrapper), 0, _Arg(o=None))
        wl.wl_proxy_wrapper_destroy(wrapper)
        self.globals = {}
        self._listener = _RegistryListener(_GLOBAL(self._on_global), _REMOVE(lambda *a: None))
        wl.wl_proxy_add_listener(self.registry, ctypes.addressof(self._listener), None)
        wl.wl_display_roundtrip_queue(display, self.queue)

        self.manager = self._bind(b"org_kde_kwin_blur_manager", self.manager_iface, 1)
        self.compositor = self._bind(b"wl_compositor", self.compositor_iface, 4)
        self.blurs = {}          # wl_surface -> org_kde_kwin_blur

    def _on_global(self, data, registry, name, interface, version):
        self.globals[interface] = (name, version)

    def _marshal(self, proxy, opcode, iface, version, flags, *args):
        arr = (_Arg * max(1, len(args)))(*args)
        return self.wl.wl_proxy_marshal_array_flags(proxy, opcode, iface, version, flags, arr)

    def _bind(self, name, iface, max_version):
        if name not in self.globals:
            return None
        gname, version = self.globals[name]
        version = min(version, max_version)
        return self._marshal(self.registry, 0, iface, version, 0,
                             _Arg(u=gname), _Arg(s=name), _Arg(u=version), _Arg(o=None))

    def set(self, surface, rects):
        if not self.manager or not self.compositor:
            return False
        blur = self.blurs.get(surface)
        if blur is None:
            blur = self._marshal(self.manager, 0, self.blur_iface, 1, 0, _Arg(o=None), _Arg(o=surface))
            self.blurs[surface] = blur
        region = None
        if rects:
            cv = self.wl.wl_proxy_get_version(self.compositor)
            region = self._marshal(self.compositor, 1, self.region_iface, cv, 0, _Arg(o=None))
            rv = self.wl.wl_proxy_get_version(region)
            for x, y, w, h in rects:
                self._marshal(region, 1, None, rv, 0, _Arg(i=x), _Arg(i=y), _Arg(i=w), _Arg(i=h))
        self._marshal(blur, 1, None, 1, 0, _Arg(o=region))
        self._marshal(blur, 0, None, 1, 0)
        if region:
            self._marshal(region, 0, None, self.wl.wl_proxy_get_version(region), _DESTROY)
        self.wl.wl_display_flush(self.display)
        return True

    def release(self, surface):
        blur = self.blurs.pop(surface, None)
        if blur:
            self._marshal(blur, 2, None, 1, _DESTROY)
        if self.manager and surface:
            self._marshal(self.manager, 1, None, 1, 0, _Arg(o=surface))
        self.wl.wl_display_flush(self.display)


def _native(qwindow, resource):
    """QPlatformNativeInterface::nativeResourceForWindow(resource, window) on Qt's Wayland plugin."""
    gui = ctypes.CDLL("libQt6Gui.so.6")
    client = ctypes.CDLL("libQt6WaylandClient.so.6")
    pni = gui._ZN15QGuiApplication23platformNativeInterfaceEv
    pni.restype = c_void_p
    fn = client._ZN15QtWaylandClient22QWaylandNativeInterface23nativeResourceForWindowERK10QByteArrayP7QWindow
    fn.restype = c_void_p
    fn.argtypes = [c_void_p, c_void_p, c_void_p]
    name = QByteArray(resource)
    return fn(pni(), shiboken6.getCppPointer(name)[0], shiboken6.getCppPointer(qwindow)[0])


def surface_of(qwindow):
    try:
        return _native(qwindow, b"surface")
    except (OSError, AttributeError):
        return None


def set_blur(qwindow, rects):
    """Blur behind `qwindow` inside `rects` (logical px, window-relative); [] means the whole window."""
    global _state
    try:
        surface = surface_of(qwindow)
        if not surface:
            return False
        if _state is None:
            display = _native(qwindow, b"display")
            if not display:
                return False
            _state = _Wayland(display)
        return _state.set(surface, rects)
    except (OSError, AttributeError, ValueError):
        return False


def clear_blur(qwindow):
    """Drop the blur before the window hides (Qt destroys the wl_surface on hide)."""
    if _state is None:
        return
    try:
        surface = surface_of(qwindow)
        if surface:
            _state.release(surface)
    except (OSError, AttributeError):
        pass


def available():
    return _state is not None and bool(_state.manager)
