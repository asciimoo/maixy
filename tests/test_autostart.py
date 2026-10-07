import argparse
import contextlib
import io
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from maixy import autostart, reload


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'Library/LaunchAgents/local.maixy.dashboard.plist'
        self.args = argparse.Namespace(socket='/tmp/custom.sock', interval=2, session='work',
                                       client='/dev/ttys1', navigator='macos', no_focus=True)
        for patcher in (patch.object(autostart, 'ROOT', self.root / 'state'),
                        patch.object(autostart.Path, 'home', return_value=self.root),
                        patch.object(autostart.sys, 'platform', 'darwin')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_plist_uses_current_interpreter_state_and_only_configuration(self):
        environment = {'PATH': '/opt/homebrew/bin:/usr/bin', 'MAIXY_NAVIGATOR': 'macos',
                       'MAIXY_AGENTS_FILE': '/custom/agents.json', 'PRIVATE_TOKEN': 'secret',
                       'TMUX': '/wrong.sock,2,0', reload.LOCK_FD_ENV: '42'}
        with patch.dict(os.environ, environment, clear=True):
            obj = autostart.specification(self.args)
        self.assertTrue(obj['KeepAlive'])
        self.assertTrue(obj['RunAtLoad'])
        self.assertEqual(obj['ProgramArguments'], [autostart.sys.executable, '-m', 'maixy',
            '--socket', '/tmp/custom.sock', '--interval', '2', '--session', 'work',
            '--client', '/dev/ttys1', '--navigator', 'macos', '--no-focus'])
        self.assertEqual(obj['EnvironmentVariables'], {'MAIXY_HOME': str(self.root / 'state'),
            'PATH': environment['PATH'], 'MAIXY_NAVIGATOR': 'macos', 'MAIXY_AGENTS_FILE': '/custom/agents.json'})
        self.assertEqual(obj['StandardOutPath'], str(self.root / 'state/dashboard.log'))
        self.assertEqual(obj['StandardOutPath'], obj['StandardErrorPath'])

    def test_install_creates_private_plist_and_bootstraps_user_service(self):
        calls = []
        def launchctl(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=1 if args[0] == 'print' else 0)
        with patch.object(autostart, 'launchctl', side_effect=launchctl), contextlib.redirect_stdout(io.StringIO()):
            autostart.install(self.args)
        target = 'gui/' + str(os.getuid()) + '/' + autostart.LABEL
        self.assertEqual(calls, [('print', target), ('enable', target),
                                 ('bootstrap', 'gui/' + str(os.getuid()), str(self.path))])
        with self.path.open('rb') as stream:
            self.assertEqual(plistlib.load(stream)['Label'], autostart.LABEL)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_install_refuses_to_compete_with_manual_dashboard(self):
        with patch.object(autostart, 'launchctl', return_value=SimpleNamespace(returncode=1)), \
             patch.object(reload, 'ROOT', self.root / 'state'), reload.acquire_lock():
            with self.assertRaisesRegex(RuntimeError, 'Stop the manually started'):
                autostart.install(self.args)
        self.assertFalse(self.path.exists())

    def test_reinstall_waits_for_old_dashboard_to_release_lock(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('old config')
        with patch.object(autostart, 'launchctl', return_value=SimpleNamespace(returncode=0)) as launchctl, \
             patch.object(autostart, 'dashboard_running', side_effect=[True, True, False]), \
             patch.object(autostart.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            autostart.install(self.args)
        self.assertEqual([call.args[0] for call in launchctl.call_args_list], ['print', 'bootout', 'enable', 'bootstrap'])
        with self.path.open('rb') as stream:
            self.assertTrue(plistlib.load(stream)['KeepAlive'])

    def test_uninstall_stops_service_and_preserves_state_and_other_jobs(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('owned config')
        unrelated = self.path.parent / 'other.plist'
        unrelated.write_text('other config')
        autostart.ROOT.mkdir()
        log = autostart.ROOT / 'dashboard.log'
        log.write_text('retain logs')
        with patch.object(autostart, 'launchctl', return_value=SimpleNamespace(returncode=0)) as launchctl, \
             contextlib.redirect_stdout(io.StringIO()):
            autostart.install(self.args, remove=True)
        self.assertEqual([call.args[0] for call in launchctl.call_args_list], ['print', 'bootout'])
        self.assertFalse(self.path.exists())
        self.assertEqual(unrelated.read_text(), 'other config')
        self.assertEqual(log.read_text(), 'retain logs')

    def test_unsupported_platform_makes_no_changes(self):
        with patch.object(autostart.sys, 'platform', 'linux'), patch.object(autostart, 'launchctl') as launchctl:
            with self.assertRaisesRegex(RuntimeError, 'macOS LaunchAgents only'):
                autostart.install(self.args)
        launchctl.assert_not_called()

    def test_launchctl_failure_reports_stderr_without_a_shell(self):
        result = SimpleNamespace(returncode=5, stderr='Bootstrap failed', stdout='')
        with patch.object(autostart.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'Bootstrap failed'):
                autostart.launchctl('bootstrap', 'gui/502', '/path with spaces/job.plist')
        self.assertEqual(run.call_args.args[0], ['/bin/launchctl', 'bootstrap', 'gui/502', '/path with spaces/job.plist'])
        self.assertNotIn('shell', run.call_args.kwargs)
