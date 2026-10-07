"""x11 graphical navigation backend."""
import re

from ..tmux import run
from .windows import unique_window


class Navigator:
    def current_window(self):
        output = run(['xprop', '-root', '_NET_ACTIVE_WINDOW']).stdout
        match = re.search(r'\b0x[0-9a-fA-F]+\b', output)
        if match:
            active = int(match[0], 16)
            for window in self._windows():
                if int(window['id'], 16) == active:
                    return window['id'], window['pid']
        raise RuntimeError('Cannot determine the active X11 window')

    def restore_window(self, window):
        if not any((item['id'], item['pid']) == window for item in self._windows()):
            raise RuntimeError('Previous X11 window is no longer available')
        run(['wmctrl', '-ia', window[0]])

    def _windows(self):
        windows = []
        for line in run(['wmctrl', '-lp']).stdout.splitlines():
            fields = line.split(None, 4)
            if len(fields) == 5 and fields[2].isdigit():
                windows.append(dict(id=fields[0], pid=int(fields[2]), name=fields[4]))
        return windows

    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        matches = [window for window in self._windows() if window['pid'] in pids]
        window = unique_window(matches, pane)
        run(['wmctrl', '-ia', window['id']])
        return
