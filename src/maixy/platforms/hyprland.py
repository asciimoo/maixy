"""hyprland graphical navigation backend."""
import json
import re

from ..tmux import run
from .windows import unique_window


class Navigator:
    def current_window(self):
        window = json.loads(run(['hyprctl', '-j', 'activewindow']).stdout)
        if not window.get('address') or not window.get('pid'):
            raise RuntimeError('Cannot determine the active Hyprland window')
        return window['address'], window['pid']

    def restore_window(self, window):
        clients = json.loads(run(['hyprctl', '-j', 'clients']).stdout)
        if not any((c.get('address'), c.get('pid')) == window for c in clients):
            raise RuntimeError('Previous Hyprland window is no longer available')
        self._focus(window[0])

    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        clients = json.loads(run(['hyprctl', '-j', 'clients']).stdout)
        matches = [dict(c, name=c.get('title', '')) for c in clients if c.get('pid') in pids]
        window = unique_window(matches, pane)
        self._focus(window['address'])

    def _focus(self, address):
        if not re.fullmatch(r'0x[0-9a-fA-F]+', address):
            raise RuntimeError('Invalid Hyprland window address')
        result = run(['hyprctl', 'dispatch', 'focuswindow', 'address:' + address], check=False)
        response = result.stdout.strip()
        if result.returncode or response != 'ok':
            # Hyprland 0.55 replaces the legacy dispatcher with Lua syntax.
            result = run(['hyprctl', 'dispatch', 'hl.dsp.focus({window = "address:' + address + '"})'], check=False)
            response = result.stdout.strip()
        if result.returncode or response != 'ok':
            raise RuntimeError('Hyprland focus failed: ' + response + '; configure MAIXY_NAVIGATION_COMMAND for a different dispatcher API')
        return
