"""Load native Windows icons at the current monitor's exact DPI sizes."""
import ctypes
import sys
from ctypes import wintypes


class WindowIcons:
    def __init__(self, window, filename):
        self.window = window
        self.filename = str(filename)
        self.dpi = None
        self.handles = []
        self.sizes = []
        self.user32 = None
        if sys.platform != 'win32':
            return
        api = self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetAncestor.restype = wintypes.HWND
        api.GetDpiForWindow.argtypes = [wintypes.HWND]
        api.GetDpiForWindow.restype = wintypes.UINT
        api.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
        api.GetSystemMetricsForDpi.restype = ctypes.c_int
        api.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        api.LoadImageW.restype = wintypes.HANDLE
        api.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        api.SendMessageW.restype = ctypes.c_ssize_t
        api.DestroyIcon.argtypes = [wintypes.HANDLE]
        api.DestroyIcon.restype = wintypes.BOOL
        window.bind('<Map>', self._changed, add='+')
        window.bind('<Configure>', self._changed, add='+')
        window.after(200, self.refresh)

    def _changed(self, event):
        if event.widget is self.window:
            self.refresh()

    def refresh(self):
        if self.user32 is None:
            return
        api = self.user32
        hwnd = api.GetAncestor(self.window.winfo_id(), 2)
        dpi = api.GetDpiForWindow(hwnd)
        attached = self.handles and all((api.SendMessageW(hwnd, 0x7F, kind, 0) & 0xFFFFFFFF) == (handle & 0xFFFFFFFF) for kind, handle in enumerate(self.handles))
        if not dpi or (dpi == self.dpi and attached):
            return
        replacements = []
        sizes = []
        try:
            for kind, xmetric, ymetric in ((0, 49, 50), (1, 11, 12)):
                width = api.GetSystemMetricsForDpi(xmetric, dpi)
                height = api.GetSystemMetricsForDpi(ymetric, dpi)
                handle = api.LoadImageW(None, self.filename, 1, width, height, 0x10)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                replacements.append((kind, handle))
                sizes.append((width, height))
        except Exception:
            for _, handle in replacements:
                api.DestroyIcon(handle)
            raise
        for kind, handle in replacements:
            api.SendMessageW(hwnd, 0x80, kind, handle)
        for handle in self.handles:
            api.DestroyIcon(handle)
        self.handles = [handle for _, handle in replacements]
        self.sizes = sizes
        self.dpi = dpi

    def close(self):
        if self.user32:
            for handle in self.handles:
                self.user32.DestroyIcon(handle)
        self.handles = []
        self.user32 = None
