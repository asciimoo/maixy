import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from maixy import dashboard, reload


class ReloadTests(unittest.TestCase):
    def test_request_rejects_missing_or_stopped_dashboard(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(reload, 'ROOT', Path(directory)):
            for present in (False, True):
                if present:
                    (reload.ROOT / 'run.lock').touch()
                with self.assertRaisesRegex(RuntimeError, 'No running maixy'):
                    reload.request_reload()
                self.assertFalse((reload.ROOT / 'reload.request').exists())

    def test_exec_failure_restores_descriptor_and_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(reload, 'ROOT', Path(directory)):
            with reload.acquire_lock() as lock, patch.object(reload.os, 'execv', side_effect=OSError('exec failed')):
                with self.assertRaisesRegex(OSError, 'exec failed'):
                    reload.restart(lock)
                self.assertFalse(os.get_inheritable(lock.fileno()))
                self.assertNotIn(reload.LOCK_FD_ENV, os.environ)

    def test_real_reload_reads_updated_code_and_keeps_pid_arguments_and_lock(self):
        # Isolated module exercises a real exec without USB access or live agents.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'maixy'
            package.mkdir()
            source = Path(reload.__file__).parent
            (package / '__init__.py').write_text('__path__.append(' + repr(str(source)) + ')\n')
            (package / 'version.txt').write_text('before')
            (package / '__main__.py').write_text('''
import json, os, sys, time
from pathlib import Path
from maixy.reload import acquire_lock, consume_request, restart
lock = acquire_lock()
version = Path(__file__).with_name('version.txt').read_text()
with open('boots.jsonl', 'a') as output:
    output.write(json.dumps({'pid': os.getpid(), 'args': sys.argv[1:], 'version': version}) + '\\n')
while not consume_request():
    time.sleep(.01)
restart(lock)
''')
            environment = dict(os.environ, PYTHONPATH=str(root), MAIXY_HOME=str(root))
            environment.pop(reload.LOCK_FD_ENV, None)
            process = subprocess.Popen([sys.executable, '-m', 'maixy', '--session', 'example'],
                                       cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def boots(count):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    filename = root / 'boots.jsonl'
                    lines = filename.read_text().splitlines() if filename.exists() else []
                    if len(lines) >= count:
                        return [json.loads(line) for line in lines]
                    if process.poll() is not None:
                        self.fail('Reload process exited: ' + process.stderr.read().decode())
                    time.sleep(.02)
                self.fail('Reload process did not start')
            try:
                initial = boots(1)[0]
                (package / 'version.txt').write_text('after')
                started = time.monotonic()
                with patch.object(reload, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                    reload.request_reload()
                    # Parent cannot take the inherited instance lock during exec.
                    while len((root / 'boots.jsonl').read_text().splitlines()) < 2:
                        with self.assertRaisesRegex(RuntimeError, 'already running'):
                            reload.acquire_lock()
                        if process.poll() is not None:
                            self.fail('Reload process exited')
                        if time.monotonic() - started > 10:
                            self.fail('Reload timed out')
                        time.sleep(.005)
                updated = boots(2)[1]
                self.assertEqual(initial['pid'], updated['pid'])
                self.assertEqual(initial['args'], updated['args'])
                self.assertEqual(updated['version'], 'after')
                self.assertFalse((root / 'reload.request').exists())
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

    def test_dashboard_closes_device_and_db_before_reloading_with_lock_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = unittest.mock.Mock()
            db = unittest.mock.Mock()
            device.poll.return_value = []
            handlers = {}
            args = argparse.Namespace(socket='test', session=None, interval=1, client=None, no_focus=True)
            def sleep(_seconds):
                handlers[signal.SIGHUP](signal.SIGHUP, None)
            def restart(lock):
                device.close.assert_called_once()
                db.close.assert_called_once()
                self.assertFalse(lock.closed)
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    reload.acquire_lock()
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(dashboard, 'connect', return_value=db))
                stack.enter_context(patch.object(reload, 'ROOT', root))
                stack.enter_context(patch.object(dashboard, 'dependencies'))
                stack.enter_context(patch.object(dashboard, 'Keypad', return_value=device))
                stack.enter_context(patch.object(dashboard, 'discover', return_value=[]))
                stack.enter_context(patch.object(dashboard, 'assign_slots', return_value=[]))
                stack.enter_context(patch.object(dashboard, 'scan_status'))
                stack.enter_context(patch.object(dashboard, 'render', return_value=b'image'))
                stack.enter_context(patch.object(dashboard.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})))
                stack.enter_context(patch.object(dashboard.time, 'sleep', side_effect=sleep))
                replacement = stack.enter_context(patch.object(dashboard, 'restart', side_effect=restart))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                dashboard.dashboard(args)
                replacement.assert_called_once()
                self.assertEqual(device.paint.call_count, 9)
                with reload.acquire_lock():
                    pass
