import signal
import struct
import subprocess
import sys
import time

from .device import Keypad, pressed_keys
from .render import render
from .state import connect, assign_slots, pane_status
from .status import StatusTracker, scan_status
from .tmux import socket_path
from .discovery import discover
from .navigation import jump, FocusToggle
from .runtime import dependencies
from .reload import acquire_lock, consume_request, restart

DISPLAY_REFRESH_INTERVAL = 15.0


def dashboard(args):
    toggle = FocusToggle() if getattr(args, 'toggle_focus', False) else None
    dependencies()
    sock = socket_path(args.socket)
    lock = acquire_lock()
    try:
        db = connect()
    except Exception:
        lock.close()
        raise
    stopped = False
    reloading = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def reload(signum, frame):
        nonlocal reloading
        reloading = True
    signal.signal(signal.SIGHUP, reload)
    device, panes, cache, held, arrows = None, [], {}, set(), set()
    tracker = StatusTracker()
    page, last_scan, last_status_scan, last_refresh, last_error = 0, float('-inf'), float('-inf'), float('-inf'), ''
    last_tick, last_wall = time.monotonic(), time.time()
    print('maixy: watching local agents and ' + sock + '; Ctrl-C to stop', flush=True)
    if toggle:
        print('maixy: window toggle enabled', flush=True)
    try:
        while not stopped:
            if reloading or consume_request():
                reloading = True
                break
            started = time.monotonic()
            wall = time.time()
            # Monotonic clocks differ in whether they advance during sleep.
            # A paused polling loop can leave valid HID handles but a reset
            # LCD; reopen and repaint even when USB reports no read error.
            resumed = max(started - last_tick, wall - last_wall) >= 5
            last_tick, last_wall = started, wall
            try:
                if resumed:
                    if device:
                        device.close()
                        device = None
                    cache.clear()
                    held.clear()
                    arrows.clear()
                    last_scan = last_status_scan = last_refresh = float('-inf')
                    print('maixy: polling resumed; reconnecting and redrawing keypad', flush=True)
                if started - last_scan >= args.interval:
                    panes = assign_slots(db, discover(sock, args.session))
                    last_scan = started
                    max_page = max((p['position'] // 9 for p in panes), default=0)
                    page = min(page, max_page)
                if started - last_status_scan >= .25:
                    scan_status(db, panes, tracker)
                    last_status_scan = started
                if device is None:
                    device = Keypad()
                    cache.clear()
                    held.clear()
                    arrows.clear()
                    print('maixy: keypad connected; ' + str(len(panes)) + ' agents', flush=True)
                visible = {p['position'] % 9: p for p in panes if p['position'] // 9 == page}
                for packet in device.poll():
                    keys = pressed_keys(packet)
                    if keys is not None:
                        for key in sorted(keys - held):
                            if key in visible:
                                try:
                                    if toggle:
                                        message = toggle.press(db, visible[key], args.client)
                                    else:
                                        jump(db, visible[key], args.client, not args.no_focus)
                                        message = 'selected ' + visible[key]['pane_title']
                                    print('maixy: ' + message, flush=True)
                                except Exception as e:
                                    print('maixy: ' + str(e), file=sys.stderr, flush=True)
                        held = keys
                    elif len(packet) >= 6 and packet[:4] == bytes([0x11, 0xff, device.controls_feature, 0]):
                        pressed = set()
                        for i in range(4, len(packet) - 1, 2):
                            value = struct.unpack_from('>H', packet, i)[0]
                            if not value:
                                break
                            pressed.add(value)
                        for key in pressed - arrows:
                            if key in (0x1a1, 0x1a2):
                                page = (page + (-1 if key == 0x1a1 else 1)) % (max_page + 1)
                        arrows = pressed
                visible = {p['position'] % 9: p for p in panes if p['position'] // 9 == page}
                previous_cache = dict(cache)
                refresh = time.monotonic()
                if device.needs_redraw() or refresh - last_refresh >= DISPLAY_REFRESH_INTERVAL:
                    cache.clear()
                    last_refresh = refresh
                for key in range(9):
                    pane = visible.get(key)
                    if pane:
                        # Discovery rebuilds pane metadata independently of
                        # status polls; preserve the latest count between them.
                        pane['background_count'] = tracker.active_children.get(pane['identity'], 0)
                    status = pane_status(db, pane) if pane else 'idle'
                    signature = (pane['identity'], pane['pane_title'], pane['window_name'],
                                 status, pane.get('background_count', 0)) if pane else None
                    if key not in cache or cache[key] != signature:
                        device.paint(key, render(pane, status, page))
                        cache[key] = signature
                        if pane and (key not in previous_cache or previous_cache[key] != signature):
                            print('maixy: key {} {} {}'.format(key + 1, pane['pane_id'], status), flush=True)
                last_error = ''
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                if str(e) != last_error:
                    print('maixy: ' + str(e) + '; retrying', file=sys.stderr, flush=True)
                    last_error = str(e)
                if device:
                    device.close()
                    device = None
                time.sleep(1)
            time.sleep(max(0, .05 - (time.monotonic() - started)))
    finally:
        if device:
            device.close()
        db.close()
        try:
            if reloading and not stopped:
                print('maixy: reloading installed code', flush=True)
                restart(lock)
        finally:
            lock.close()
