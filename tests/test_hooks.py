import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maixy import hooks, state
from maixy.agents.claude import Claude


class HookTests(unittest.TestCase):
    def test_claude_question_hooks_wait_and_resume(self):
        adapter = Claude()
        self.assertEqual(adapter.hook_status('PreToolUse', {'tool_name': 'AskUserQuestion'}), 'waiting')
        self.assertEqual(adapter.hook_status('PostToolUse', {'tool_name': 'AskUserQuestion'}), 'working')
        self.assertEqual(adapter.hook_status('PreToolUse', {'tool_name': 'Bash'}), 'working')
        self.assertIsNone(adapter.hook_status('PreToolUse', {'tool_name': 'AskUserQuestion', 'agent_id': 'child'}))

    def test_install_preserves_existing_hooks_and_uninstall_removes_only_ours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.claude/settings.json'
            path.parent.mkdir()
            existing = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'existing-bell'}]}]}, 'unrelated': 'keep'}
            path.write_text(json.dumps(existing))
            with patch.object(hooks.Path, 'home', return_value=root), patch.object(hooks, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                hooks.install_hooks()
                hooks.install_hooks()
                result = json.loads(path.read_text())
                self.assertEqual(result['unrelated'], 'keep')
                self.assertEqual(len(result['hooks']['Stop']), 2)
                hooks.install_hooks(remove=True)
            self.assertEqual(json.loads(path.read_text()), existing)
            self.assertTrue(list(root.glob('claude-hooks-backup-*.json')))

    def test_legacy_and_module_hooks_are_recognized(self):
        self.assertTrue(hooks.is_maixy_hook('/Users/test/bin/logiai hook codex Stop'))
        self.assertTrue(hooks.is_maixy_hook('/path/python -m maixy hook codex Stop'))
        self.assertFalse(hooks.is_maixy_hook('/path/python -m another_tool hook codex Stop'))

    def test_input_hook_reports_session_without_tmux(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(state, 'ROOT', Path(directory)), \
             patch.dict(hooks.os.environ, {}, clear=True), \
             patch.object(hooks.sys, 'stdin', io.StringIO('{"session_id":"outside"}')), \
             contextlib.redirect_stdout(io.StringIO()):
            hooks.hook('claude', 'PermissionRequest')
            with contextlib.closing(state.connect()) as db:
                row = db.execute('SELECT * FROM session_events').fetchone()
                self.assertEqual((row['agent'], row['session'], row['status']), ('claude', 'outside', 'waiting'))
