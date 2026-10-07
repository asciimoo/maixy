"""Maixy command-line interface."""
import argparse
import contextlib
import subprocess
import sys

from . import __version__
from .agents import Agent, registry
from .config import ROOT
from .dashboard import dashboard
from .hooks import hook, install_hooks
from .runtime import dependencies, install_dependencies
from .state import connect, assign_slots, pane_status
from .status import StatusTracker, scan_status
from .tmux import socket_path
from .discovery import discover
from .navigation import jump, configure, selected_name, backend
from .reload import request_reload
from .autostart import install as install_autostart

DESCRIPTION = "Live Logitech MX Keypad dashboard for local coding agents."


def main():
    ap = argparse.ArgumentParser(description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=__version__)
    ap.add_argument('command', nargs='?', default='run', choices=['run', 'reload', 'agents', 'list', 'doctor', 'install-deps', 'install-hooks', 'uninstall-hooks', 'install-autostart', 'uninstall-autostart', 'hook', 'jump'])
    ap.add_argument('extra', nargs='*')
    ap.add_argument('--socket', help='tmux server socket (default: inherited TMUX or default server)')
    ap.add_argument('--session', help='only include this tmux session')
    ap.add_argument('--client', help='tmux client tty to switch (default: most recently active)')
    ap.add_argument('--no-focus', action='store_true', help='select tmux panes without bringing their host application forward')
    ap.add_argument('--interval', type=float, default=1, help='agent discovery interval in seconds')
    ap.add_argument('--navigator', help='auto, macos, x11, sway, hyprland, custom, headless, plugin name, or module:factory (default: MAIXY_NAVIGATOR or auto)')
    args = ap.parse_args()
    configure(args.navigator)
    if args.command == 'reload':
        request_reload()
    elif args.command in ('install-autostart', 'uninstall-autostart'):
        install_autostart(args, remove=args.command == 'uninstall-autostart')
    elif args.command == 'agents':
        for adapter in registry().adapters.values():
            native = type(adapter).event_status is not Agent.event_status
            screen = bool(adapter.display) or type(adapter).display_status is not Agent.display_status
            print('{}\tnative={}\tscreen={}\tinstall-hooks={}'.format(
                adapter.name, native, screen, adapter.hook_path() is not None))
    elif args.command == 'hook':
        if len(args.extra) != 2:
            ap.error('hook needs AGENT EVENT')
        hook(*args.extra)
    elif args.command == 'install-deps':
        install_dependencies()
    elif args.command in ('install-hooks', 'uninstall-hooks'):
        ROOT.mkdir(parents=True, exist_ok=True)
        install_hooks(args.command == 'uninstall-hooks')
    elif args.command == 'run':
        if args.interval <= 0:
            ap.error('--interval must be positive')
        dashboard(args)
    else:
        sock = socket_path(args.socket)
        with contextlib.closing(connect()) as db:
            panes = assign_slots(db, discover(sock, args.session))
            if args.command in ('list', 'doctor'):
                scan_status(db, panes, StatusTracker())
            if args.command == 'jump':
                if len(args.extra) != 1 or not args.extra[0].isdigit() or int(args.extra[0]) < 1:
                    ap.error('jump needs a key number (1-based)')
                target = next((p for p in panes if p['position'] == int(args.extra[0]) - 1), None)
                if not target:
                    raise RuntimeError('No agent assigned to that key')
                jump(db, target, args.client, not args.no_focus)
            else:
                for p in panes:
                    print('{}\t{}\t{}\t{}\t{}'.format(p['position'] + 1, p['agent'], p['pane_id'], pane_status(db, p), p['pane_title']))
                if args.command == 'doctor':
                    dependencies()
                    import hid
                    print('USB interfaces: ' + str(len(hid.enumerate(0x046d, 0xc354))))
                    navigator = backend()
                    print('Process platform: ' + sys.platform)
                    print('Navigator: ' + selected_name() + ' (' + type(navigator).__module__ + ')')
                    print('Agent adapters: ' + ', '.join(registry().adapters))
                    print('Status backend: agent adapters; native events, hooks, and supported terminal screens.')
                    print('Discovery: tmux, standalone terminals, and editor agent processes; exact editor-terminal selection uses the optional bridge.')


def entrypoint():
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print("maixy: " + str(error), file=sys.stderr)
        return 1
    return 0
