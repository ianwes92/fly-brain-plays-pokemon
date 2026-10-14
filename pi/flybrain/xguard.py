"""Keep an X11 error from killing the program before it can save.

When the play window is closed with the touchpad, it is destroyed while
pointer events for it are still queued; SDL then asks the X server about a
window that no longer exists (BadWindow, in the XInput extension), and Xlib's
default error handler exits the process on the spot -- before the save on
exit runs. That happened repeatedly, and turning off SDL's XInput2 support did
not prevent it.

install() replaces the handler with one that records the error instead of
exiting. A BadWindow naming our own window means the window is gone, and the
play loop treats that exactly like the close button: stop, save, quit.
Other X errors are recorded and ignored.
"""
import ctypes
import ctypes.util

BAD_WINDOW = 3


class XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int),
                ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong),
                ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte),
                ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_HANDLER_T = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))


class XGuard:
    def __init__(self):
        self.window = None
        self.window_lost = False
        self.errors = []
        self._cb = None           # must stay referenced, or ctypes frees the callback

    def install(self, window_id):
        """Call after the SDL window exists (SDL installs its own handler at init)."""
        path = ctypes.util.find_library("X11")
        if not path:
            return False
        lib = ctypes.CDLL(path)
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        lib.XSetErrorHandler.argtypes = [_HANDLER_T]
        self.window = int(window_id)

        def handler(display, event):
            e = event.contents
            self.errors.append((e.error_code, e.request_code, e.minor_code, e.resourceid))
            if e.error_code == BAD_WINDOW and e.resourceid == self.window:
                self.window_lost = True
            return 0

        self._cb = _HANDLER_T(handler)
        lib.XSetErrorHandler(self._cb)
        return True
