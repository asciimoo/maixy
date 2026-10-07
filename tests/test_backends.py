import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from maixy import navigation


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.configuration = patch.object(navigation, '_selection', None)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)

    def selected(self, environment, platform='linux'):
        with patch.dict(os.environ, environment, clear=True), patch.object(navigation.sys, 'platform', platform):
            return navigation.selected_name()

    def test_auto_detects_desktop_and_keeps_unknown_wayland_out_of_x11(self):
        for environment, expected in [
            ({'SWAYSOCK': '/sway', 'DISPLAY': ':0'}, 'sway'),
            ({'HYPRLAND_INSTANCE_SIGNATURE': 'test', 'DISPLAY': ':0'}, 'hyprland'),
            ({'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'}, 'headless'),
            ({'DISPLAY': ':0'}, 'x11'), ({}, 'headless'),
            ({'MAIXY_NAVIGATION_COMMAND': '["custom"]'}, 'custom'),
        ]:
            with self.subTest(environment=environment):
                self.assertEqual(self.selected(environment), expected)
        self.assertEqual(self.selected({}, 'darwin'), 'macos')

    def test_cli_selection_overrides_environment(self):
        navigation.configure('sway')
        self.assertEqual(self.selected({'MAIXY_NAVIGATOR': 'x11'}), 'sway')

    def test_python_plugin_can_load_without_changing_core(self):
        navigator = SimpleNamespace(focus=lambda pane, foreground=True: None)
        module = SimpleNamespace(Navigator=lambda: navigator)
        navigation.configure('my_desktop:Navigator')
        with patch.object(navigation.importlib, 'import_module', return_value=module) as load:
            self.assertIs(navigation.backend(), navigator)
        load.assert_called_once_with('my_desktop')

    def test_packaged_entrypoint_plugin_can_load_without_changing_core(self):
        navigator = SimpleNamespace(focus=lambda pane, foreground=True: None)
        entry = SimpleNamespace(name='my-wm', load=lambda: lambda: navigator)
        navigation.configure('my-wm')
        with patch.object(navigation.metadata, 'entry_points', return_value={'maixy.navigators': [entry]}):
            self.assertIs(navigation.backend(), navigator)

    def test_invalid_plugin_and_unknown_backend_fail_clearly(self):
        navigation.configure('invalid:Navigator')
        with patch.object(navigation.importlib, 'import_module', return_value=SimpleNamespace(Navigator=lambda: object())):
            with self.assertRaisesRegex(RuntimeError, 'must implement focus'):
                navigation.backend()
        navigation.configure('missing')
        with patch.object(navigation.metadata, 'entry_points', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'Unknown or ambiguous'):
                navigation.backend()

    def test_headless_backend_keeps_external_completion_unacknowledged(self):
        navigation.configure('headless')
        with self.assertRaisesRegex(RuntimeError, 'No graphical navigator'):
            navigation.backend().focus({})
