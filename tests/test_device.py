import argparse
import contextlib
import io
from pathlib import Path
import signal
import threading
import tempfile
import unittest
from unittest.mock import patch

from maixy import dashboard, device, reload, state
from maixy.device import Keypad, image_packets, pressed_keys
from maixy.status import observe


class ProtocolTests(unittest.TestCase):
    def keypad(self):
        keypad = Keypad.__new__(Keypad)
        keypad._write_lock = threading.RLock()
        keypad._stop = threading.Event()
        keypad._redraw = threading.Event()
        keypad._worker = None
        keypad._worker_error = None
        keypad._last_heartbeat = None
        keypad.keepalive_feature, keypad.brightness_feature = 4, 15
        keypad.handles = {}
        return keypad

    def test_keepalive_runs_while_main_thread_is_busy_and_stops_on_close(self):
        keypad = self.keypad()
        received = threading.Event()
        with patch.object(keypad, 'write', side_effect=lambda packet: received.set()), \
             patch.object(device, 'KEEPALIVE_INTERVAL', .01):
            keypad._worker = threading.Thread(target=keypad._keepalive)
            keypad._worker.start()
            try:
                self.assertTrue(received.wait(1), 'No background keepalive while caller was blocked')
            finally:
                keypad.close()
            self.assertFalse(keypad._worker.is_alive())

    def test_keepalive_failure_is_reported_to_dashboard(self):
        keypad = self.keypad()
        with patch.object(keypad, 'heartbeat', side_effect=OSError('USB unplugged')), \
             patch.object(device, 'KEEPALIVE_INTERVAL', .01):
            keypad._worker = threading.Thread(target=keypad._keepalive)
            keypad._worker.start()
            try:
                keypad._worker.join(1)
                self.assertFalse(keypad._worker.is_alive())
                with self.assertRaisesRegex(OSError, 'USB unplugged'):
                    keypad.needs_redraw()
                with self.assertRaisesRegex(OSError, 'USB unplugged'):
                    keypad.poll()
            finally:
                keypad.close()

    def test_keepalive_deadline_invalidates_display_on_either_clock(self):
        for mono, wall in ((103, 103), (100, 160)):
            with self.subTest(mono=mono, wall=wall):
                keypad = self.keypad()
                keypad._last_heartbeat = (100, 100)
                with patch.object(keypad, 'write') as write, \
                     patch.object(device.time, 'monotonic', return_value=mono), \
                     patch.object(device.time, 'time', return_value=wall):
                    keypad.heartbeat()
                self.assertEqual(write.call_args_list[0].args[0][:6], bytes([0x11, 0xff, 4, 0x1b, 0x0b, 0xb8]))
                self.assertTrue(keypad.needs_redraw())
                self.assertFalse(keypad.needs_redraw())

    def test_jpeg_fragmentation_preserves_payload_and_geometry(self):
        for size in (1, 4075, 4076, 12000):
            with self.subTest(size=size):
                jpeg = bytes(i % 251 for i in range(size))
                packets = list(image_packets(8, jpeg))
                restored = packets[0][20:] + b''.join(packet[5:] for packet in packets[1:])
                self.assertEqual(restored[:size], jpeg)
                self.assertTrue(all(len(packet) == 4095 for packet in packets))
                self.assertTrue(packets[0][4] & 0x80)
                self.assertTrue(packets[-1][4] & 0x40)
                self.assertEqual(int.from_bytes(packets[0][9:11], 'big'), 339)
                self.assertEqual(int.from_bytes(packets[0][11:13], 'big'), 322)

    def test_oversized_image_is_rejected(self):
        with self.assertRaises(ValueError):
            list(image_packets(0, bytes(65536)))

    def test_button_press_release_and_unrelated_reports(self):
        self.assertEqual(pressed_keys(bytes([0x13, 0xff, 2, 0, 0, 1, 1, 3, 0])), {0, 2})
        self.assertEqual(pressed_keys(bytes([0x13, 0xff, 2, 0, 0, 1, 0])), set())
        self.assertIsNone(pressed_keys(bytes([0x11, 0xff, 2, 0])))


class ReconnectTests(unittest.TestCase):
    def test_background_count_repaints_parent_without_extra_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pane = dict(identity='test:%1:10', socket='test', pane_id='%1', pane_pid='10',
                        agent='claude', pane_title='Parent agent', window_name='work')
            with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                observe(db, pane, 'working', 1)
            handlers, paints, counts, clock = {}, [], [], [100.0]

            class FakeDevice:
                def poll(self):
                    return []

                def needs_redraw(self):
                    return False

                def paint(self, key, image):
                    paints.append((key, image))

                def close(self):
                    pass

            def scan(db, panes, tracker):
                count = (2, 1, 0)[len(counts)]
                counts.append(count)
                tracker.active_children = {pane['identity']: count} if count else {}

            def sleep(_seconds):
                if len(counts) == 3:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                clock[0] += .1

            args = argparse.Namespace(socket='test', session=None, interval=.1, client=None, no_focus=True)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(state, 'ROOT', root))
                stack.enter_context(patch.object(reload, 'ROOT', root))
                stack.enter_context(patch.object(dashboard, 'dependencies'))
                stack.enter_context(patch.object(dashboard, 'Keypad', return_value=FakeDevice()))
                # Refresh metadata between status scans, as live discovery does.
                discover = stack.enter_context(patch.object(dashboard, 'discover', side_effect=lambda *args: [dict(pane)]))
                stack.enter_context(patch.object(dashboard, 'scan_status', side_effect=scan))
                stack.enter_context(patch.object(dashboard, 'render', side_effect=lambda pane, status, page:
                                             (status, pane.get('background_count', 0)) if pane else None))
                stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                stack.enter_context(patch.object(dashboard.time, 'monotonic', side_effect=lambda: clock[0]))
                stack.enter_context(patch.object(dashboard.time, 'time', return_value=100))
                stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                dashboard.dashboard(args)
                self.assertGreater(discover.call_count, len(counts))
            self.assertEqual([key for key, _ in paints], list(range(9)) + [0, 0])
            self.assertEqual([image for key, image in paints if key == 0],
                             [('working', 2), ('working', 1), ('working', 0)])
            with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM slots').fetchone()[0], 1)

    def test_silent_display_reset_repaints_unchanged_and_empty_keys(self):
        for notification in (True, False):
            with self.subTest(notification=notification), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                panes = [dict(identity='test:%{}:10'.format(i), socket='test', pane_id='%{}'.format(i),
                              pane_pid='10', agent='codex', pane_title='Test {}'.format(i), window_name='work')
                         for i in range(2)]
                with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                    observe(db, panes[0], 'working', 1)
                    observe(db, panes[0], 'idle', 2)
                    observe(db, panes[1], 'working', 1)
                handlers, devices = {}, []
                clock = [100.0]
                ticks = [0]

                class FakeDevice:
                    def __init__(self):
                        self.painted, self.display, self.reset = [], {}, False
                        devices.append(self)

                    def poll(self):
                        return []

                    def needs_redraw(self):
                        reset, self.reset = self.reset, False
                        return reset

                    def paint(self, key, image):
                        self.painted.append(key)
                        self.display[key] = image

                    def close(self):
                        pass

                def sleep(_seconds):
                    keypad = devices[0]
                    if ticks[0] == 0:
                        keypad.display.clear()  # Firmware replaced the LCD with its logo.
                        keypad.reset = notification
                        with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                            observe(db, panes[1], 'waiting', 3)
                    if len(keypad.painted) >= 18:
                        expected = list(range(9)) + ([] if notification else [1]) + list(range(9))
                        self.assertEqual(keypad.painted, expected)
                        handlers[signal.SIGTERM](signal.SIGTERM, None)
                    ticks[0] += 1
                    self.assertLess(ticks[0], 20, 'Silent reset was not repaired')
                    clock[0] += .05 if notification else 1.0

                args = argparse.Namespace(socket='test', session=None, interval=1, client=None, no_focus=True)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(state, 'ROOT', root))
                    stack.enter_context(patch.object(reload, 'ROOT', root))
                    stack.enter_context(patch.object(dashboard, 'dependencies'))
                    stack.enter_context(patch.object(dashboard, 'Keypad', side_effect=FakeDevice))
                    stack.enter_context(patch.object(dashboard, 'discover', return_value=panes))
                    stack.enter_context(patch.object(dashboard, 'scan_status'))
                    stack.enter_context(patch.object(dashboard, 'render', side_effect=lambda pane, status, page: status))
                    stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                    stack.enter_context(patch.object(dashboard.time, 'monotonic', side_effect=lambda: clock[0]))
                    stack.enter_context(patch.object(dashboard.time, 'time', return_value=100))
                    stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    dashboard.dashboard(args)
                self.assertEqual(len(devices), 1)
                self.assertEqual(devices[0].display, dict(enumerate(['done', 'waiting'] + ['idle'] * 7)))
                with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                    self.assertEqual(state.pane_status(db, panes[0]), 'done')

    def test_wake_reopens_healthy_handles_and_redraws_all_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pane = dict(identity='test:%1:10', socket='test', pane_id='%1', pane_pid='10',
                        agent='codex', pane_title='Test agent', window_name='work')
            with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                observe(db, pane, 'working', 1)
                observe(db, pane, 'idle', 2)
            handlers, devices = {}, []
            clocks = {'wall': 100, 'mono': 100}

            class FakeDevice:
                def __init__(self):
                    self.painted, self.closed = [], False
                    devices.append(self)

                def heartbeat(self):
                    pass

                def needs_redraw(self):
                    return False

                def poll(self):
                    return []

                def paint(self, key, image):
                    self.painted.append((key, image))

                def close(self):
                    self.closed = True

            def sleep(_seconds):
                if len(devices) == 1:
                    # Simulate sleep while the monotonic clock does not move.
                    clocks['wall'] += 60
                elif len(devices) == 2:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                else:
                    self.fail('Unexpected repeated reconnect')

            args = argparse.Namespace(socket='test', session=None, interval=1, client=None, no_focus=True)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(state, 'ROOT', root))
                stack.enter_context(patch.object(reload, 'ROOT', root))
                stack.enter_context(patch.object(dashboard, 'dependencies'))
                stack.enter_context(patch.object(dashboard, 'Keypad', side_effect=FakeDevice))
                stack.enter_context(patch.object(dashboard, 'discover', return_value=[pane]))
                stack.enter_context(patch.object(dashboard, 'scan_status'))
                stack.enter_context(patch.object(dashboard, 'render', side_effect=lambda pane, status, page: status))
                stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                stack.enter_context(patch.object(dashboard.time, 'time', side_effect=lambda: clocks['wall']))
                stack.enter_context(patch.object(dashboard.time, 'monotonic', side_effect=lambda: clocks['mono']))
                stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                dashboard.dashboard(args)
            self.assertEqual(len(devices), 2)
            self.assertTrue(all(device.closed for device in devices))
            self.assertEqual([key for key, _ in devices[1].painted], list(range(9)))
            self.assertEqual(devices[1].painted[0][1], 'done')
            with patch.object(state, 'ROOT', root), contextlib.closing(state.connect()) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM slots').fetchone()[0], 1)
                self.assertEqual(state.pane_status(db, pane), 'done')

    def test_disconnect_retry_and_repaint_preserve_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pane = dict(identity='test:%1:10', socket='test', pane_id='%1', pane_pid='10',
                        agent='codex', pane_title='Test agent', window_name='work')
            with patch.object(state, 'ROOT', root):
                db = state.connect()
                observe(db, pane, 'working', 1)
                observe(db, pane, 'idle', 2)
                db.close()
            handlers, devices = {}, []
            opened = []

            class FakeDevice:
                controls_feature = 11

                def __init__(self, disconnect=False):
                    self.disconnect = disconnect
                    self.painted, self.polls, self.closed = [], 0, False

                def heartbeat(self):
                    pass

                def needs_redraw(self):
                    return False

                def poll(self):
                    self.polls += 1
                    if self.disconnect and self.polls > 1:
                        raise OSError('USB unplugged')
                    return []

                def paint(self, index, image):
                    self.painted.append((index, image))

                def close(self):
                    self.closed = True

            def open_device():
                opened.append(True)
                if len(opened) == 2:
                    raise RuntimeError('MX Keypad not connected')
                device = FakeDevice(disconnect=len(opened) == 1)
                devices.append(device)
                return device

            def sleep(_seconds):
                if len(devices) == 2 and len(devices[-1].painted) == 9:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                if len(opened) > 5:
                    self.fail('Dashboard failed to recover')

            args = argparse.Namespace(socket='test', session=None, interval=1, client=None, no_focus=True)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(state, 'ROOT', root))
                stack.enter_context(patch.object(reload, 'ROOT', root))
                stack.enter_context(patch.object(dashboard, 'dependencies'))
                stack.enter_context(patch.object(dashboard, 'Keypad', side_effect=open_device))
                stack.enter_context(patch.object(dashboard, 'discover', return_value=[pane]))
                stack.enter_context(patch.object(dashboard, 'scan_status'))
                stack.enter_context(patch.object(dashboard, 'render', side_effect=lambda pane, status, page: status))
                stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                # Python 3.9 on macOS may start its monotonic clock near zero.
                # Advance past the polling interval without real sleeps.
                stack.enter_context(patch.object(dashboard.time, 'monotonic', return_value=100))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                dashboard.dashboard(args)
            self.assertEqual(len(opened), 3)
            self.assertTrue(all(device.closed for device in devices))
            self.assertEqual([index for index, _ in devices[1].painted], list(range(9)))
            self.assertEqual(devices[1].painted[0][1], 'done')
