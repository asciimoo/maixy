import contextlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from maixy import navigation, state
from maixy.status import observe
from maixy.platforms import sway, x11, hyprland, custom
from maixy.platforms.windows import unique_window


class NavigationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.patch = patch.object(state, 'ROOT', Path(directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.db = state.connect()
        self.addCleanup(self.db.close)
        self.pane = dict(kind='process', socket='local', pane_id='pid:11', pane_pid='11:start', agent='codex')
        observe(self.db, self.pane, 'working', 1)
        observe(self.db, self.pane, 'idle', 2)

    def test_completion_acknowledged_only_after_external_selection_succeeds(self):
        with patch.object(navigation, 'backend') as backend:
            backend.return_value.focus.side_effect = RuntimeError('Tab disappeared')
            with self.assertRaises(RuntimeError):
                navigation.jump(self.db, self.pane)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            backend.return_value.focus.side_effect = None
            navigation.jump(self.db, self.pane)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_editor_selects_exact_terminal_before_acknowledgment(self):
        pane = dict(self.pane, editor_bridge={'token': 'secret'}, terminal_pid=10)
        with patch.object(navigation.editor, 'select') as select, patch.object(navigation, 'backend'):
            navigation.jump(self.db, pane)
            select.assert_called_once_with(pane)
        self.assertEqual(state.pane_status(self.db, pane), 'idle')

    def test_custom_backend_passes_data_as_json_without_shell_or_bridge_tokens(self):
        pane = dict(self.pane, pane_title='$(unsafe)', editor_bridge={'token': 'secret'})
        with patch.dict(os.environ, {'MAIXY_NAVIGATION_COMMAND': '["/bin/navigator", "--focus"]'}), \
             patch.object(custom.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            custom.Navigator().focus(pane)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/bin/navigator', '--focus'])
        self.assertNotIn('shell', kwargs)
        data = json.loads(kwargs['input'])
        self.assertEqual(data['pane_title'], '$(unsafe)')
        self.assertNotIn('editor_bridge', data)

    def test_sway_selects_window_owned_by_an_ancestor(self):
        tree = {'nodes': [{'id': 42, 'type': 'con', 'pid': 1, 'name': 'Project'}]}
        with patch.dict(os.environ, {'SWAYSOCK': '/sway.sock'}, clear=True), \
             patch.object(sway, 'run', side_effect=[SimpleNamespace(stdout=json.dumps(tree)), SimpleNamespace(stdout='[{"success":true}]')]) as run:
            sway.Navigator().focus(dict(self.pane, host_chain=[11, 10, 1]))
        self.assertEqual(run.call_args.args[0], ['swaymsg', '[con_id=42]', 'focus'])

    def test_ambiguous_linux_windows_require_a_custom_navigator(self):
        with self.assertRaises(RuntimeError):
            unique_window([{'id': 1}, {'id': 2}], {})

    def test_x11_activates_matching_window(self):
        with patch.dict(os.environ, {'DISPLAY': ':0'}, clear=True), \
             patch.object(x11, 'run', side_effect=[SimpleNamespace(stdout='0x012 0 1 machine Project\n0x013 0 2 machine Other'), SimpleNamespace(stdout='')]) as run:
            x11.Navigator().focus(dict(self.pane, host_chain=[11, 10, 1]))
        self.assertEqual(run.call_args.args[0], ['wmctrl', '-ia', '0x012'])

    def test_hyprland_current_dispatcher_fallback(self):
        clients = [{'pid': 1, 'address': '0xabc', 'title': 'Project'}]
        with patch.dict(os.environ, {'HYPRLAND_INSTANCE_SIGNATURE': 'test'}, clear=True), \
             patch.object(hyprland, 'run', side_effect=[
                 SimpleNamespace(stdout=json.dumps(clients)),
                 SimpleNamespace(stdout='Invalid dispatcher', returncode=1),
                 SimpleNamespace(stdout='ok', returncode=0)]) as run:
            hyprland.Navigator().focus(dict(self.pane, host_chain=[11, 10, 1]))
        self.assertEqual(run.call_args.args[0], ['hyprctl', 'dispatch', 'hl.dsp.focus({window = "address:0xabc"})'])

    def test_sway_failed_focus_is_not_a_successful_selection(self):
        tree = {'nodes': [{'id': 42, 'type': 'con', 'pid': 1, 'name': 'Project'}]}
        with patch.dict(os.environ, {'SWAYSOCK': '/sway.sock'}, clear=True), \
             patch.object(sway, 'run', side_effect=[SimpleNamespace(stdout=json.dumps(tree)),
                                                  SimpleNamespace(stdout='[{"success":false}]')]):
            with self.assertRaises(RuntimeError):
                sway.Navigator().focus(dict(self.pane, host_chain=[11, 10, 1]))
