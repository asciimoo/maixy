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


def jump(db, pane, client=None, foreground=True):
    if pane.get('kind', 'tmux') == 'tmux':
        # tmux selects stable IDs and records acknowledgment after success.
        tmux.jump(db, pane, client, foreground=False)
        if foreground:
            fmt = tmux.SEP.join(['#{client_name}', '#{client_activity}', '#{client_tty}'])
            clients = [line.split(tmux.SEP) for line in tmux.tmux(pane['socket'], 'list-clients', '-F', fmt).stdout.splitlines()]
            clients = [c for c in clients if len(c) == 3 and (not client or client in (c[0], c[2]))]
            selected = max(clients, key=lambda c: int(c[1] or 0), default=None)
            if selected:
                from .processes import inventory
                from .hosts import identify
                records = inventory()
                matches = [r for r in records.values() if r['tty'] == selected[2]]
                host = identify(matches[0]['pid'], records) if matches else dict(host='Terminal', host_chain=[])
                target = dict(pane, pane_tty=selected[2], **host)
                try:
                    backend().focus(target)
                except (RuntimeError, OSError) as error:
                    print('maixy: selected tmux pane; host focus failed: ' + str(error), file=sys.stderr)
        return
    if 'editor_bridge' in pane:
        editor.select(pane)
        if foreground:
            backend().focus(pane)
    else:
        backend().focus(pane, foreground)
    # Failed external navigation must leave the finished key green.
    with db:
        db.execute('UPDATE events SET acknowledged=? WHERE socket=? AND pane=?',
                   (time.time(), pane['socket'], pane['pane_id']))
