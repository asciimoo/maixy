"""sway graphical navigation backend."""
import json

from ..tmux import run
from .windows import nodes, unique_window


class Navigator:
    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        tree = json.loads(run(['swaymsg', '-t', 'get_tree', '-r']).stdout)
        matches = [n for n in nodes(tree) if n.get('pid') in pids and n.get('type') == 'con']
        window = unique_window(matches, pane)
        response = run(['swaymsg', '[con_id=' + str(window['id']) + ']', 'focus']).stdout
        if not response or not all(item.get('success') for item in json.loads(response)):
            raise RuntimeError('Sway could not focus the selected agent window')
        return
