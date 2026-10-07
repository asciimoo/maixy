"""Live /proc, PTY, session tracking, and tmux checks; no AI requests or USB."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import sqlite3
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

from maixy import discovery, processes, state, tmux
from maixy.status import StatusTracker, scan_status
from maixy.platforms.x11 import Navigator as X11Navigator


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux /proc integration')
class LinuxIntegrationTests(unittest.TestCase):
    def test_real_standalone_pty_and_owned_session_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            log = root / 'session.jsonl'
            started = dict(type='event_msg', timestamp='2026-10-04T12:00:01Z', payload={'type': 'task_started'})
            log.write_text(json.dumps(started) + '\n')
            codex_home = root / '.codex'
            codex_home.mkdir()
            with contextlib.closing(sqlite3.connect(codex_home / 'state_5.sqlite')) as database:
                database.execute('CREATE TABLE threads(id,name,rollout_path,cwd,source,archived)')
                database.execute('INSERT INTO threads VALUES(?,?,?,?,?,0)',
                                 ('test-root', 'Standalone task', str(log), str(project), 'cli'))
                database.commit()
            master, slave = pty.openpty()
            child_code = '''import ctypes, os, sys, time
ctypes.CDLL(None).prctl(15, b'codex', 0, 0, 0)
stream = open(sys.argv[1])
print('READY', flush=True)
time.sleep(30)
'''

            def own_terminal():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            child = subprocess.Popen([sys.executable, '-c', child_code, str(log)], cwd=project,
                                     stdin=slave, stdout=slave, stderr=slave, preexec_fn=own_terminal)
            os.close(slave)
            try:
                self.assertTrue(select.select([master], [], [], 5)[0], 'Agent stub did not start')
                self.assertIn(b'READY', os.read(master, 1024))
                inventory = processes.inventory()
                record = inventory[child.pid]
                self.assertEqual(processes.agent_name(record), 'codex')
                self.assertTrue(record['tty'].startswith('/dev/pts/'))
                details = processes.files([child.pid])[child.pid]
                self.assertEqual(details['cwd'], str(project))
                self.assertIn(str(log), details['logs'])
                with patch.object(Path, 'home', return_value=root), patch.object(state, 'ROOT', root / 'state'):
                    panes = discovery.discover(str(root / 'absent-tmux.sock'))
                    pane = next(p for p in panes if p['agent_pid'] == child.pid)
                    self.assertEqual(pane['kind'], 'process')
                    self.assertEqual(pane['thread_id'], 'test-root')
                    with contextlib.closing(state.connect()) as database:
                        tracker = StatusTracker()
                        scan_status(database, [pane], tracker)
                        self.assertEqual(state.pane_status(database, pane), 'working')
                        completed = dict(started, timestamp='2026-10-04T12:00:02Z', payload={'type': 'task_complete'})
                        with log.open('a') as stream:
                            stream.write(json.dumps(completed) + '\n')
                        scan_status(database, [pane], tracker)
                        self.assertEqual(state.pane_status(database, pane), 'done')
            finally:
                child.terminate()
                child.wait(timeout=5)
                os.close(master)

    def test_real_tmux_enumeration_and_selection_without_desktop(self):
        import shutil
        if not shutil.which('tmux'):
            self.skipTest('tmux not installed')
        with tempfile.TemporaryDirectory() as directory:
            sock = str(Path(directory) / 'server.sock')
            tmux.tmux(sock, 'new-session', '-d', '-s', 'test', '-n', 'agent')
            try:
                result = tmux.tmux(sock, 'list-panes', '-F', '#{pane_id}')
                pane = result.stdout.strip()
                self.assertTrue(pane.startswith('%'))
                tmux.tmux(sock, 'select-pane', '-t', pane, '-T', 'Linux test')
                self.assertEqual(tmux.tmux(sock, 'display-message', '-p', '-t', pane, '#{pane_title}').stdout.strip(), 'Linux test')
            finally:
                tmux.tmux(sock, 'kill-server', check=False)

    def test_real_x11_focus_chooses_the_agent_window(self):
        import shutil
        if not all(shutil.which(program) for program in ('Xvfb', 'openbox', 'xterm', 'wmctrl', 'xprop')):
            self.skipTest('Xvfb/openbox/xterm/wmctrl/xprop not installed')
        children = []
        read_fd, write_fd = os.pipe()
        try:
            server = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '640x480x24', '-nolisten', 'tcp'],
                                      pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.append(server)
            os.close(write_fd)
            write_fd = None
            self.assertTrue(select.select([read_fd], [], [], 5)[0], 'Xvfb did not start')
            display = ':' + os.read(read_fd, 100).decode().strip()
            with patch.dict(os.environ, {'DISPLAY': display}):
                window_manager = subprocess.Popen(['openbox'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                children.append(window_manager)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if 'name: openbox' in tmux.run(['wmctrl', '-m'], check=False).stdout.lower():
                        break
                    time.sleep(.05)
                terminals = []
                for title in ('Maixy target', 'Other terminal'):
                    child = subprocess.Popen(['xterm', '-fa', 'DejaVu Sans Mono', '-fs', '10', '-T', title, '-e', '/bin/sleep', '30'],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    children.append(child)
                    terminals.append(child)
                target = None
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    windows = [line.split(None, 4) for line in tmux.run(['wmctrl', '-lp'], check=False).stdout.splitlines()]
                    matches = [window for window in windows if len(window) == 5 and window[2] == str(terminals[0].pid)]
                    if matches and len(windows) >= 2:
                        target = matches[0][0]
                        break
                    time.sleep(.05)
                self.assertIsNotNone(target, 'Agent terminal window was not created: '
                                     + str([(t.pid, t.poll()) for t in terminals]) + '; windows=' + str(windows))
                X11Navigator().focus(dict(host_chain=[terminals[0].pid], window_title='Maixy target'))
                deadline = time.monotonic() + 3
                active = 0
                while time.monotonic() < deadline:
                    result = tmux.run(['xprop', '-root', '_NET_ACTIVE_WINDOW']).stdout
                    try:
                        active = int(result.split()[-1], 16)
                    except ValueError:
                        pass
                    if active == int(target, 16):
                        break
                    time.sleep(.05)
                self.assertEqual(active, int(target, 16))
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
