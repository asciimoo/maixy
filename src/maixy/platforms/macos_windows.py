"""Retain exact macOS Accessibility windows without keystrokes or title matching."""
from contextlib import contextmanager
import ctypes


class Window:
    """Own the retained application/window references until the toggle resets."""
    def __init__(self, api, application, window):
        self.api, self.application, self.window = api, application, window

    def close(self):
        for name in ('window', 'application'):
            value = getattr(self, name)
            if value:
                self.api.cf.CFRelease(value)
                setattr(self, name, None)

    def __del__(self):
        self.close()


class Accessibility:
    def __init__(self):
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        pointer = ctypes.c_void_p
        signatures = (
            (self.cf, 'CFStringCreateWithCString', [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer),
            (self.cf, 'CFRelease', [pointer], None),
            (self.ax, 'AXIsProcessTrusted', [], ctypes.c_bool),
            (self.ax, 'AXUIElementCreateSystemWide', [], pointer),
            (self.ax, 'AXUIElementSetMessagingTimeout', [pointer, ctypes.c_float], ctypes.c_int),
            (self.ax, 'AXUIElementCopyAttributeValue', [pointer, pointer, ctypes.POINTER(pointer)], ctypes.c_int),
            (self.ax, 'AXUIElementPerformAction', [pointer, pointer], ctypes.c_int),
            (self.ax, 'AXUIElementSetAttributeValue', [pointer, pointer, pointer], ctypes.c_int),
        )
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        self.true = pointer.in_dll(self.cf, 'kCFBooleanTrue').value

    @contextmanager
    def string(self, value):
        reference = self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)
        if not reference:
            raise RuntimeError('Cannot create macOS Accessibility attribute')
        try:
            yield reference
        finally:
            self.cf.CFRelease(reference)

    def copy(self, element, attribute):
        value = ctypes.c_void_p()
        with self.string(attribute) as name:
            error = self.ax.AXUIElementCopyAttributeValue(element, name, ctypes.byref(value))
        if error or not value.value:
            if value.value:
                self.cf.CFRelease(value.value)
            raise RuntimeError('Cannot access macOS window (' + attribute + ', error ' + str(error) + ')')
        return value.value

    def current_window(self):
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError('--toggle-focus requires macOS Accessibility permission for Maixy\'s Python process')
        system = self.ax.AXUIElementCreateSystemWide()
        application = None
        try:
            self.ax.AXUIElementSetMessagingTimeout(system, 2.0)
            application = self.copy(system, 'AXFocusedApplication')
            window = self.copy(application, 'AXFocusedWindow')
            return Window(self, application, window)
        except Exception:
            if application:
                self.cf.CFRelease(application)
            raise
        finally:
            self.cf.CFRelease(system)

    def restore_window(self, window):
        # Reading the role checks that the retained object still exists. An AX
        # reference is tied to this window, even when its title or order changes.
        if not window.window or not window.application:
            raise RuntimeError('Previous macOS window is no longer available')
        self.cf.CFRelease(self.copy(window.window, 'AXRole'))
        with self.string('AXRaise') as action:
            error = self.ax.AXUIElementPerformAction(window.window, action)
        if not error:
            with self.string('AXFrontmost') as attribute:
                error = self.ax.AXUIElementSetAttributeValue(window.application, attribute, self.true)
        if error:
            raise RuntimeError('Could not restore previous macOS window (Accessibility error ' + str(error) + ')')
