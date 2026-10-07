import contextlib
import ctypes
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from maixy import autostart, cli, dashboard, navigation, reload, state
from maixy.platforms import hyprland, sway, x11
from maixy.platforms.macos_windows import Accessibility, Window
from maixy.status import observe


class ToggleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        patcher = patch.object(state, 'ROOT', root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = state.connect()
        self.addCleanup(self.db.close)
        self.pane = dict(kind='process', socket='local', pane_id='pid:11', pane_pid='11:start',
                         identity='local:11:start', agent='codex', pane_title='Agent')
        observe(self.db, self.pane, 'working', 1)
        observe(self.db, self.pane, 'idle', 2)
        self.navigator = SimpleNamespace(current_window=Mock(return_value='original'),
                                         restore_window=Mock(), focus=Mock())
        patcher = patch.object(navigation, 'backend', return_value=self.navigator)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.toggle = navigation.FocusToggle()
        patcher = patch.object(navigation.time, 'time', return_value=2.5)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_repeated_presses_alternate_and_only_agent_selection_acknowledges(self):
        self.toggle.press(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
        observe(self.db, self.pane, 'working', 3)
        observe(self.db, self.pane, 'idle', 4)
        self.assertEqual(self.toggle.press(self.db, self.pane), 'returned to previous window')
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
        self.clock.return_value = 4.5
        self.toggle.press(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
        self.toggle.press(self.db, self.pane)
        self.assertEqual(self.navigator.focus.call_count, 2)
        self.assertEqual(self.navigator.restore_window.call_count, 2)
        self.navigator.restore_window.assert_called_with('original')
        self.navigator.current_window.assert_called_once_with()

    def test_another_agent_or_reused_pid_captures_a_new_origin(self):
        self.toggle.press(self.db, self.pane)
        self.navigator.current_window.return_value = 'first agent window'
        pane = dict(self.pane, identity='local:11:new-start', pane_pid='11:new-start')
        self.toggle.press(self.db, pane)
        self.toggle.press(self.db, pane)
        self.assertEqual(self.navigator.current_window.call_count, 2)
        self.navigator.restore_window.assert_called_once_with('first agent window')

    def test_failed_jump_preserves_green_and_does_not_arm_return(self):
        self.navigator.focus.side_effect = RuntimeError('Window disappeared')
        with self.assertRaises(RuntimeError):
            self.toggle.press(self.db, self.pane)
        self.assertFalse(self.toggle.at_target)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
        self.navigator.focus.side_effect = None
        self.toggle.press(self.db, self.pane)
        self.navigator.restore_window.assert_not_called()

    def test_failed_return_retains_origin_for_retry_without_acknowledgment(self):
        self.toggle.press(self.db, self.pane)
        observe(self.db, self.pane, 'working', 3)
        observe(self.db, self.pane, 'idle', 4)
        self.navigator.restore_window.side_effect = RuntimeError('Window unavailable')
        with self.assertRaises(RuntimeError):
            self.toggle.press(self.db, self.pane)
        self.assertTrue(self.toggle.at_target)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
        self.navigator.restore_window.side_effect = None
        self.toggle.press(self.db, self.pane)
        self.assertFalse(self.toggle.at_target)

    def test_unsupported_backend_does_not_select(self):
        self.toggle.navigator = SimpleNamespace(focus=self.navigator.focus)
        with self.assertRaisesRegex(RuntimeError, 'does not support'):
            self.toggle.press(self.db, self.pane)
        self.navigator.focus.assert_not_called()

    def test_capture_failure_selects_normally_and_retries_when_access_is_granted(self):
        for failure in (None, RuntimeError('Accessibility permission missing'), OSError('Unavailable')):
            with self.subTest(failure=failure):
                self.navigator.current_window.side_effect = failure if isinstance(failure, Exception) else None
                self.navigator.current_window.return_value = None
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.toggle.press(self.db, self.pane)
                self.assertIn('selecting agent without toggle', errors.getvalue())
                self.assertIsNone(self.toggle.identity)
                self.assertFalse(self.toggle.at_target)
                self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
        self.navigator.current_window.side_effect = None
        self.navigator.current_window.return_value = 'original'
        self.toggle.press(self.db, self.pane)
        self.toggle.press(self.db, self.pane)
        self.navigator.restore_window.assert_called_once_with('original')

    def test_tmux_host_failure_preserves_green_and_toggle_state(self):
        pane = dict(self.pane, kind='tmux', session_id='$1', window_id='@1')
        self.navigator.focus.side_effect = RuntimeError('Host unavailable')
        with patch.object(navigation.tmux, 'jump', return_value='/dev/ttys1') as select, \
             patch('maixy.processes.inventory', return_value={}):
            with self.assertRaises(RuntimeError):
                self.toggle.press(self.db, pane)
        select.assert_called_once_with(self.db, pane, None, foreground=False, acknowledge=False)
        self.assertEqual(state.pane_status(self.db, pane), 'done')
        self.assertFalse(self.toggle.at_target)

    def test_editor_return_does_not_select_the_terminal_again(self):
        pane = dict(self.pane, editor_bridge={'token': 'secret'})
        with patch.object(navigation.editor, 'select') as select:
            self.toggle.press(self.db, pane)
            self.toggle.press(self.db, pane)
        select.assert_called_once_with(pane)


class WindowSnapshotTests(unittest.TestCase):
    def test_x11_captures_and_restores_exact_id_and_pid(self):
        listing = '0x000012 0 123 host Original\n0x000013 0 123 host Other'
        with patch.object(x11, 'run', side_effect=[
                SimpleNamespace(stdout='_NET_ACTIVE_WINDOW(WINDOW): window id # 0x12'),
                SimpleNamespace(stdout=listing), SimpleNamespace(stdout=listing),
                SimpleNamespace(stdout='')]) as run:
            navigator = x11.Navigator()
            window = navigator.current_window()
            navigator.restore_window(window)
        self.assertEqual(window, ('0x000012', 123))
        self.assertEqual(run.call_args.args[0], ['wmctrl', '-ia', '0x000012'])

    def test_sway_captures_floating_window_and_restores_by_container(self):
        tree = {'nodes': [{'id': 1, 'type': 'con', 'pid': 4}],
                'floating_nodes': [{'id': 42, 'type': 'con', 'pid': 5, 'focused': True}]}
        with patch.object(sway, 'run', side_effect=[
                SimpleNamespace(stdout=json.dumps(tree)), SimpleNamespace(stdout=json.dumps(tree)),
                SimpleNamespace(stdout='[{"success":true}]')]) as run:
            navigator = sway.Navigator()
            window = navigator.current_window()
            navigator.restore_window(window)
        self.assertEqual(window, (42, 5))
        self.assertEqual(run.call_args.args[0], ['swaymsg', '[con_id=42]', 'focus'])

    def test_hyprland_captures_and_restores_by_address(self):
        active = {'address': '0xabc', 'pid': 5}
        with patch.object(hyprland, 'run', side_effect=[
                SimpleNamespace(stdout=json.dumps(active)), SimpleNamespace(stdout=json.dumps([active])),
                SimpleNamespace(stdout='ok', returncode=0)]) as run:
            navigator = hyprland.Navigator()
            window = navigator.current_window()
            navigator.restore_window(window)
        self.assertEqual(window, ('0xabc', 5))
        self.assertEqual(run.call_args.args[0], ['hyprctl', 'dispatch', 'focuswindow', 'address:0xabc'])

    def test_closed_or_reused_windows_are_not_focused(self):
        cases = [(x11, ('0x12', 5), '0x12 0 6 host Reused'),
                 (sway, (42, 5), json.dumps({'nodes': [{'id': 42, 'pid': 6}]})),
                 (hyprland, ('0xabc', 5), json.dumps([{'address': '0xabc', 'pid': 6}]))]
        for module, window, listing in cases:
            with self.subTest(module=module), patch.object(module, 'run', return_value=SimpleNamespace(stdout=listing)) as run:
                with self.assertRaisesRegex(RuntimeError, 'no longer available'):
                    module.Navigator().restore_window(window)
                self.assertEqual(run.call_count, 1)

    def test_absent_focused_window_is_reported(self):
        for module, output in ((x11, '_NET_ACTIVE_WINDOW: window id # 0x0'),
                               (sway, '{}'), (hyprland, '{}')):
            with self.subTest(module=module), patch.object(module, 'run', return_value=SimpleNamespace(stdout=output)):
                with self.assertRaisesRegex(RuntimeError, 'Cannot determine'):
                    module.Navigator().current_window()


class MacWindowTests(unittest.TestCase):
    def setUp(self):
        # Mock native calls: these tests never access the user's desktop.
        self.api = Accessibility.__new__(Accessibility)
        self.api.cf = SimpleNamespace(CFRelease=Mock(), CFStringCreateWithCString=Mock(side_effect=lambda _, name, encoding: name))
        self.api.ax = SimpleNamespace(AXIsProcessTrusted=Mock(return_value=True),
                                     AXUIElementCreateSystemWide=Mock(return_value=1),
                                     AXUIElementSetMessagingTimeout=Mock(),
                                     AXUIElementPerformAction=Mock(return_value=0),
                                     AXUIElementSetAttributeValue=Mock(return_value=0))
        self.api.true = 99

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS native API loading')
    def test_native_api_loads_without_inspecting_or_changing_user_windows(self):
        api = Accessibility()
        system = api.ax.AXUIElementCreateSystemWide()
        self.assertTrue(system)
        api.cf.CFRelease(system)

    def test_snapshot_owns_references_and_return_raises_exact_window(self):
        self.api.copy = Mock(side_effect=[2, 3, 4])
        window = self.api.current_window()
        self.api.restore_window(window)
        self.api.ax.AXUIElementPerformAction.assert_called_once_with(3, b'AXRaise')
        self.api.ax.AXUIElementSetAttributeValue.assert_called_once_with(2, b'AXFrontmost', 99)
        window.close()
        window.close()
        self.assertEqual([call.args[0] for call in self.api.cf.CFRelease.call_args_list],
                         [1, 4, b'AXRaise', b'AXFrontmost', 3, 2])

    def test_capture_failure_releases_application_and_system_references(self):
        self.api.copy = Mock(side_effect=[2, RuntimeError('No window')])
        with self.assertRaises(RuntimeError):
            self.api.current_window()
        self.assertEqual([call.args[0] for call in self.api.cf.CFRelease.call_args_list], [2, 1])

    def test_missing_permission_reports_requirement(self):
        self.api.ax.AXIsProcessTrusted.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Accessibility permission'):
            self.api.current_window()
        self.api.ax.AXUIElementCreateSystemWide.assert_not_called()

    def test_closed_window_and_failed_raise_do_not_activate_another_window(self):
        window = Window(self.api, 2, 3)
        self.addCleanup(window.close)
        self.api.copy = Mock(side_effect=RuntimeError('Window closed'))
        with self.assertRaises(RuntimeError):
            self.api.restore_window(window)
        self.api.ax.AXUIElementPerformAction.assert_not_called()
        self.api.copy.side_effect = None
        self.api.copy.return_value = 4
        self.api.ax.AXUIElementPerformAction.return_value = -25202
        with self.assertRaises(RuntimeError):
            self.api.restore_window(window)
        self.api.ax.AXUIElementSetAttributeValue.assert_not_called()

    def test_copy_uses_output_pointer_and_releases_attribute_string(self):
        def copy(element, name, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 42
            return 0
        self.api.ax.AXUIElementCopyAttributeValue = Mock(side_effect=copy)
        self.assertEqual(self.api.copy(1, 'AXFocusedWindow'), 42)
        self.api.cf.CFRelease.assert_called_once_with(b'AXFocusedWindow')


class ToggleOptionTests(unittest.TestCase):
    def test_cli_passes_option_to_dashboard(self):
        with patch.object(cli.sys, 'argv', ['maixy', '--toggle-focus']), \
             patch.object(cli, 'configure'), patch.object(cli, 'dashboard') as run:
            cli.main()
        self.assertTrue(run.call_args.args[0].toggle_focus)

    def test_cli_rejects_no_focus_and_one_shot_jump(self):
        for arguments in (['--toggle-focus', '--no-focus'], ['jump', '1', '--toggle-focus']):
            with self.subTest(arguments=arguments), patch.object(cli.sys, 'argv', ['maixy', *arguments]), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main()
            self.assertEqual(error.exception.code, 2)

    def test_autostart_preserves_toggle_option(self):
        args = SimpleNamespace(socket='/tmp/test.sock', interval=1, no_focus=False, toggle_focus=True)
        self.assertIn('--toggle-focus', autostart.specification(args)['ProgramArguments'])

    def test_dashboard_toggles_once_per_press_and_retains_state_across_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pane = dict(kind='process', identity='local:11:start', socket='local', pane_id='pid:11',
                        pane_pid='11:start', agent='codex', pane_title='Agent', window_name='Terminal')
            handlers = {}
            reports = iter(([b'press'], [b'press'], [b'release'], OSError('disconnected'),
                            [b'press'], [b'release'], [b'press']))
            class Device:
                def poll(self):
                    result = next(reports)
                    if isinstance(result, Exception):
                        raise result
                    return result
                def paint(self, *args):
                    pass
                def needs_redraw(self):
                    return False
                def close(self):
                    pass
            toggle = Mock()
            toggle.press.return_value = 'toggled'
            def sleep(_):
                if toggle.press.call_count == 3:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
            args = SimpleNamespace(socket='test', session=None, client=None, interval=1,
                                   no_focus=False, toggle_focus=True)
            with contextlib.ExitStack() as stack:
                for module in (state, reload):
                    stack.enter_context(patch.object(module, 'ROOT', root))
                stack.enter_context(patch.object(dashboard, 'dependencies'))
                stack.enter_context(patch.object(dashboard, 'discover', return_value=[pane]))
                stack.enter_context(patch.object(dashboard, 'scan_status'))
                stack.enter_context(patch.object(dashboard, 'Keypad', side_effect=Device))
                stack.enter_context(patch.object(dashboard, 'FocusToggle', return_value=toggle))
                stack.enter_context(patch.object(dashboard, 'pressed_keys', side_effect=lambda p: {0} if p == b'press' else set()))
                stack.enter_context(patch.object(dashboard, 'render', return_value=b'image'))
                stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                dashboard.dashboard(args)
            self.assertEqual(toggle.press.call_count, 3)
