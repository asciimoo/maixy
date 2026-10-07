from collections import defaultdict
import contextlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from maixy import state, tmux
from maixy.status import observe


class TmuxTests(unittest.TestCase):
    def test_agent_is_found_under_the_pane_shell(self):
        children = defaultdict(list, {10: [11], 11: [12]})
        names = {10: 'zsh', 11: 'launcher', 12: '/path/claude'}
        self.assertEqual(tmux.agent_process(10, children, names), ('claude', 12))

    def test_jump_uses_stable_ids_and_acknowledges_only_after_selection(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(state, 'ROOT', Path(directory)):
            db = state.connect()
            self.addCleanup(db.close)
            pane = dict(socket='test', pane_id='%3', pane_pid='10', agent='codex', session_id='$1', window_id='@2')
            observe(db, pane, 'working', 1)
            observe(db, pane, 'idle', 2)
            clients = tmux.SEP.join(['/dev/ttys000', '$1', '100', '/dev/ttys000'])
            calls = []

            def command(sock, *args, **kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=clients if args[0] == 'list-clients' else '')

            with patch.object(tmux, 'tmux', side_effect=command):
                tmux.jump(db, pane, foreground=False)
            self.assertEqual(calls[1:], [
                ('switch-client', '-c', '/dev/ttys000', '-t', '$1'),
                ('select-window', '-t', '@2'),
                ('select-pane', '-t', '%3'),
            ])
            self.assertEqual(state.pane_status(db, pane), 'idle')

    def test_failed_selection_keeps_completion_green(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(state, 'ROOT', Path(directory)):
            db = state.connect()
            self.addCleanup(db.close)
            pane = dict(socket='test', pane_id='%3', pane_pid='10', agent='codex', session_id='$1', window_id='@2')
            observe(db, pane, 'working', 1)
            observe(db, pane, 'idle', 2)
            clients = tmux.SEP.join(['/dev/ttys000', '$1', '100', '/dev/ttys000'])
            with patch.object(tmux, 'tmux', side_effect=[SimpleNamespace(stdout=clients), RuntimeError('client disconnected')]):
                with self.assertRaises(RuntimeError):
                    tmux.jump(db, pane, foreground=False)
            self.assertEqual(state.pane_status(db, pane), 'done')

