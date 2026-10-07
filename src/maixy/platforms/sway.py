"""sway graphical navigation backend."""
import json

from ..tmux import run
from .windows import nodes, unique_window


class Navigator:
    def current_window(self):
        tree = json.loads(run(['swaymsg', '-t', 'get_tree', '-r']).stdout)
        matches = [n for n in nodes(tree) if n.get('focused') and n.get('pid')
                   and n.get('type') == 'con']
        if len(matches) != 1:
            raise RuntimeError('Cannot determine the active Sway window')
        return matches[0]['id'], matches[0]['pid']

    def restore_window(self, window):
        tree = json.loads(run(['swaymsg', '-t', 'get_tree', '-r']).stdout)
        if not any((n.get('id'), n.get('pid')) == window for n in nodes(tree)):
            raise RuntimeError('Previous Sway window is no longer available')
        self._focus(window[0])

    def _focus(self, window_id):
        response = run(['swaymsg', '[con_id=' + str(window_id) + ']', 'focus']).stdout
        results = json.loads(response) if response else []
        if not results or not all(item.get('success') for item in results):
            raise RuntimeError('Sway could not focus the selected window')

    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        tree = json.loads(run(['swaymsg', '-t', 'get_tree', '-r']).stdout)
        matches = [n for n in nodes(tree) if n.get('pid') in pids and n.get('type') == 'con']
        window = unique_window(matches, pane)
        self._focus(window['id'])
        return
