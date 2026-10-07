"""hyprland graphical navigation backend."""
import json
import re

from ..tmux import run
from .windows import unique_window


class Navigator:
    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        clients = json.loads(run(['hyprctl', '-j', 'clients']).stdout)
        matches = [dict(c, name=c.get('title', '')) for c in clients if c.get('pid') in pids]
        window = unique_window(matches, pane)
        address = window['address']
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
