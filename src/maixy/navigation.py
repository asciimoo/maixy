"""Navigation dispatcher. Backends implement focus(pane, foreground=True)."""
import os
import sys
import time
import importlib
from importlib import metadata

from . import editor, tmux


BUILTINS = {
    name: 'maixy.platforms.' + name for name in
    ('macos', 'x11', 'sway', 'hyprland', 'custom', 'headless')
}
_selection = None


def configure(selection):
    global _selection
    _selection = selection


def selected_name():
    name = _selection or os.environ.get('MAIXY_NAVIGATOR', 'auto')
    if name != 'auto':
        return name
    if os.environ.get('MAIXY_NAVIGATION_COMMAND'):
        return 'custom'
    if sys.platform == 'darwin':
        return 'macos'
    if sys.platform.startswith('linux'):
        if os.environ.get('SWAYSOCK'):
            return 'sway'
        if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            return 'hyprland'
        if os.environ.get('WAYLAND_DISPLAY'):
            return 'headless'  # XWayland's DISPLAY cannot focus native windows.
        if os.environ.get('DISPLAY'):
            return 'x11'
    return 'headless'


def backend():
    name = selected_name()
    try:
        if name in BUILTINS:
            navigator = importlib.import_module(BUILTINS[name]).Navigator()
        elif ':' in name:
            module, factory = name.split(':', 1)
            navigator = getattr(importlib.import_module(module), factory)()
        else:
            entries = metadata.entry_points()
            plugins = entries.select(group='maixy.navigators') if hasattr(entries, 'select') else entries.get('maixy.navigators', ())
            matches = [entry for entry in plugins if entry.name == name]
            if len(matches) != 1:
                raise RuntimeError('Unknown or ambiguous navigator: ' + name)
            navigator = matches[0].load()()
    except (ImportError, AttributeError, TypeError) as error:
        raise RuntimeError('Cannot load navigator ' + name + ': ' + str(error)) from error
    if not callable(getattr(navigator, 'focus', None)):
        raise RuntimeError('Navigator ' + name + ' must implement focus(pane, foreground=True)')
    return navigator


class FocusToggle:
    """Remember one return window for consecutive presses of the same agent."""
    def __init__(self):
        self.navigator = None
        self.identity = None
        self.origin = None
        self.at_target = False

    def press(self, db, pane, client=None):
        if self.navigator is None:
            self.navigator = backend()
        navigator = self.navigator
        if not all(callable(getattr(navigator, method, None))
                   for method in ('current_window', 'restore_window')):
            raise RuntimeError('Navigator does not support --toggle-focus; '
                               'it needs current_window() and restore_window(window)')
        identity = pane.get('identity') or (pane['socket'], pane['pane_id'], pane['pane_pid'])
        if identity == self.identity and self.at_target:
            navigator.restore_window(self.origin)
            self.at_target = False
            return 'returned to previous window'
        try:
            origin = self.origin if identity == self.identity else navigator.current_window()
            if origin is None:
                raise RuntimeError('Cannot determine the previous window for --toggle-focus')
        except (RuntimeError, OSError) as error:
            # Missing desktop permissions must not disable ordinary selection.
            # Retry capture on the next press so granting access takes effect.
            print('maixy: window toggle unavailable: ' + str(error) +
                  '; selecting agent without toggle', file=sys.stderr, flush=True)
            jump(db, pane, client, navigator=navigator)
            self.identity, self.origin, self.at_target = None, None, False
            return 'selected ' + pane['pane_title']
        jump(db, pane, client, navigator=navigator)
        # Commit toggle state only after successful navigation. Returning never
        # acknowledges a completion that occurred while viewing the agent.
        self.identity, self.origin, self.at_target = identity, origin, True
        return 'selected ' + pane['pane_title']


def jump(db, pane, client=None, foreground=True, navigator=None):
    if pane.get('kind', 'tmux') == 'tmux':
        # Acknowledge only after both pane selection and host focus succeed.
        tty = tmux.jump(db, pane, client, foreground=False, acknowledge=False)
        if foreground:
            from .processes import inventory
            from .hosts import identify
            records = inventory()
            matches = [r for r in records.values() if r['tty'] == tty]
            host = identify(matches[0]['pid'], records) if matches else dict(host='Terminal', host_chain=[])
            target = dict(pane, pane_tty=tty, **host)
            (navigator or backend()).focus(target)
    elif 'editor_bridge' in pane:
        editor.select(pane)
        if foreground:
            (navigator or backend()).focus(pane)
    else:
        (navigator or backend()).focus(pane, foreground)
    # Failed external navigation must leave the finished key green.
    with db:
        db.execute('UPDATE events SET acknowledged=? WHERE socket=? AND pane=?',
                   (time.time(), pane['socket'], pane['pane_id']))
