from collections import defaultdict
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from maixy import agents, discovery, hooks, processes, state, tmux
from maixy.agents import Agent, Registry
from maixy.status import StatusTracker, scan_status


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / 'agents.json'
        self.config.write_text('{}')
        self.environment = patch.dict(agents.os.environ, {'MAIXY_AGENTS_FILE': str(self.config)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.entries = patch.object(agents.metadata, 'entry_points', return_value={})
        self.entries.start()
        self.addCleanup(self.entries.stop)
        agents.registry.cache_clear()
        self.addCleanup(agents.registry.cache_clear)

    def configure(self, obj):
        self.config.write_text(json.dumps(obj))
        agents.registry.cache_clear()
        return agents.registry()

    def identify(self, name, argv=()):
        reader = Mock(return_value=argv)
        result = agents.process_agent(dict(name=name, pid=42), reader)
        return result, reader

    def test_builtins_match_exact_executables(self):
        for name in ('codex', 'claude', 'gemini', 'aider', 'opencode'):
            with self.subTest(name=name):
                result, reader = self.identify('/opt/bin/' + name)
                self.assertEqual(result, name)
                reader.assert_not_called()
        self.assertEqual(self.identify('claude-code')[0], 'claude')
        self.assertIsNone(self.identify('my-codex-wrapper')[0])

    def test_interpreter_entrypoints_and_python_modules(self):
        for executable, argv, expected in [
            ('node', ['node', '/opt/node_modules/@google/gemini-cli/bundle/gemini.js'], 'gemini'),
            ('bun', ['bun', '/opt/node_modules/opencode-ai/bin/opencode'], 'opencode'),
            ('python3.9', ['python3.9', '/opt/venv/bin/aider'], 'aider'),
            ('python', ['python', '-u', '-m', 'aider'], 'aider'),
            ('python3', ['python3', '-m', 'aider.main'], 'aider'),
        ]:
            with self.subTest(argv=argv):
                self.assertEqual(self.identify(executable, argv)[0], expected)

    def test_argument_mentions_do_not_discover_an_agent(self):
        for name, argv in [
            ('node', ['node', '/work/server.js', '/opt/@google/gemini-cli/index.js']),
            ('python3', ['python3', '-c', 'aider']),
            ('node', ['node', '-e', 'codex']),
            ('bash', ['bash', '/bin/codex']),
        ]:
            self.assertIsNone(self.identify(name, argv)[0])

    def test_json_agent_matches_native_script_and_module(self):
        self.configure({'agents': [dict(name='custom', executables=['custom-cli'],
                                       scripts=['*/custom-package/cli.js'], modules=['custom_agent'])]})
        self.assertEqual(self.identify('custom-cli')[0], 'custom')
        self.assertEqual(self.identify('node', ['node', '/opt/custom-package/cli.js'])[0], 'custom')
        self.assertEqual(self.identify('python3', ['python3', '-m', 'custom_agent'])[0], 'custom')

    def test_state_directory_config_and_missing_optional_config(self):
        with patch.dict(agents.os.environ, {}, clear=True), patch.object(agents, 'ROOT', self.root):
            self.config.write_text('{"agents":[{"name":"custom","executables":["custom"]}]}')
            agents.registry.cache_clear()
            self.assertIsNotNone(agents.registry().get('custom'))
            self.config.unlink()
            agents.registry.cache_clear()
            self.assertEqual(len(agents.registry().adapters), 5)

    def test_explicit_missing_configuration_reports_error(self):
        self.config.unlink()
        with self.assertRaisesRegex(RuntimeError, 'Cannot load agent adapters'):
            agents.registry()

    def test_invalid_configuration_reports_actionable_error(self):
        for obj in [[], {'agents': 'custom'}, {'unknown': []},
                    {'agents': [dict(name='custom')]},
                    {'agents': [dict(name='custom', executables=['Custom'])]},
                    {'agents': [dict(name='Bad Name', executables=['custom'])]},
                    {'agents': [dict(name='custom', executables=['custom'], display={'idle': ['ready']})]},
                    {'agents': [dict(name='custom', executables=['custom'], display={'idle': ['^(']})]},
                    {'agents': [dict(name='custom', executables=['custom'], display={'done': ['^done']})]}]:
            with self.subTest(obj=obj), self.assertRaises(RuntimeError):
                self.configure(obj)

    def test_duplicate_and_ambiguous_agents_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Duplicate agent adapter'):
            self.configure({'agents': [dict(name='codex', executables=['other'])]})
        self.configure({'agents': [dict(name='other', executables=['codex'])]})
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous agent process match'):
            self.identify('codex')

    def test_builtin_can_be_disabled_or_replaced_explicitly(self):
        self.configure({'disabled': ['gemini']})
        self.assertIsNone(self.identify('gemini')[0])
        self.configure({'disabled': ['gemini'], 'agents': [dict(
            name='gemini', executables=['gemini'], display={'idle': ['^Custom Gemini ready>$']})]})
        self.assertEqual(self.identify('gemini')[0], 'gemini')
        self.assertEqual(agents.registry().get('gemini').display_status('Custom Gemini ready>'), 'idle')
        with self.assertRaisesRegex(RuntimeError, 'unknown built-in adapters'):
            self.configure({'disabled': ['misspelled']})

    def test_module_factory_and_packaged_plugins(self):
        factory = Mock(return_value=Agent('module-agent', ('module-cli',)))
        with patch.object(agents.importlib, 'import_module', return_value=SimpleNamespace(Factory=factory)) as imported:
            self.configure({'plugins': ['my_agents:Factory']})
        imported.assert_called_once_with('my_agents')
        self.assertEqual(self.identify('module-cli')[0], 'module-agent')
        entry = SimpleNamespace(name='packaged', load=lambda: lambda: Agent('packaged', ('packaged-cli',)))
        with patch.object(agents.metadata, 'entry_points', return_value={'maixy.agents': [entry]}):
            self.configure({})
            self.assertEqual(self.identify('packaged-cli')[0], 'packaged')

    def test_modern_entrypoint_api(self):
        entry = SimpleNamespace(name='packaged', load=lambda: lambda: Agent('packaged', ('packaged-cli',)))
        entries = Mock()
        entries.select.return_value = [entry]
        with patch.object(agents.metadata, 'entry_points', return_value=entries):
            self.configure({})
        entries.select.assert_called_once_with(group='maixy.agents')
        self.assertEqual(self.identify('packaged-cli')[0], 'packaged')

    def test_tmux_finds_registered_agents_under_shells(self):
        self.configure({'agents': [dict(name='custom', executables=['custom-cli'])]})
        for name, expected in [('gemini', 'gemini'), ('aider', 'aider'), ('opencode', 'opencode'),
                               ('custom-cli', 'custom'), ('custom', 'custom')]:
            self.assertEqual(tmux.agent_process(10, defaultdict(list, {10: [11]}),
                                               {10: 'zsh', 11: name}), (expected, 11))

    def test_standalone_discovery_ignores_workers_and_bare_servers(self):
        records = {pid: dict(pid=pid, parent=parent, name=name, tty=tty, started='start')
                   for pid, parent, name, tty in [(12, 11, 'gemini', '/dev/tty1'),
                                                 (11, 1, 'gemini', '/dev/tty1'),
                                                 (21, 1, 'aider', '/dev/tty2'),
                                                 (31, 1, 'opencode', '/dev/tty3'),
                                                 (41, 1, 'opencode', '')]}
        files = {pid: dict(cwd='/work', logs=[]) for pid in records}
        with patch.object(processes, 'inventory', return_value=records), \
             patch.object(processes, 'files', return_value=files), \
             patch.object(discovery.tmux, 'discover', return_value=[]), \
             patch.object(discovery.hosts, 'terminal_tabs', return_value={}), \
             patch.object(discovery.editor, 'bridges', return_value=[]):
            panes = discovery.discover('/test')
        self.assertEqual({p['agent_pid'] for p in panes}, {11, 21, 31})

    def test_json_screen_completion_and_unknown_status(self):
        self.configure({'agents': [dict(name='custom', executables=['custom'], display={
            'working': ['^Busy \\(esc to stop\\)$'], 'waiting': ['^Approve action\\?$'], 'idle': ['^Ready>$']})]})
        pane = dict(agent='custom', kind='process', pane_pid='42:start', agent_pid=42,
                    socket='local', pane_id='pid:42', identity='local:42:start', pane_title='Custom')
        with patch.object(state, 'ROOT', self.root), contextlib.closing(state.connect()) as db:
            tracker = StatusTracker()
            for screen, expected, now in [('Ready>', 'idle', 0), ('Busy (esc to stop)', 'working', 1),
                                          ('Approve action?', 'waiting', 2), ('Ready>', 'waiting', 3),
                                          ('Ready>', 'done', 5)]:
                with patch('maixy.status.host_screen', return_value=screen), \
                     patch('maixy.status.time.monotonic', return_value=now):
                    scan_status(db, [pane], tracker)
                self.assertEqual(state.pane_status(db, pane), expected)
            db.execute('UPDATE events SET acknowledged=updated')
            db.commit()
            with patch('maixy.status.host_screen', return_value='Ready>'):
                scan_status(db, [pane], tracker)
            self.assertEqual(state.pane_status(db, pane), 'idle')
            pane.update(agent='gemini', pane_pid='43:start', pane_id='pid:43', identity='local:43:start')
            with patch('maixy.status.host_screen', return_value='Ready>'):
                scan_status(db, [pane], tracker)
            self.assertEqual(state.pane_status(db, pane), 'unknown')

    def test_plugin_native_status_and_session_hooks(self):
        class Custom(Agent):
            def event_status(self, obj):
                return obj.get('status')

            def session_id(self, pane):
                return 'custom-session'

        adapter = Custom('custom', ('custom',))
        pane = dict(agent='custom', kind='process', pane_pid='42:start', agent_pid=42,
                    socket='local', pane_id='pid:42', identity='local:42:start', pane_title='Custom',
                    session_path=str(self.root / 'live.jsonl'))
        path = Path(pane['session_path'])
        path.write_text('{"status":"done","timestamp":"2026-10-04T12:00:01Z"}\n')
        with patch('maixy.status.registry', return_value=Registry([adapter])), \
             patch('maixy.status.host_screen', return_value=None), \
             patch.object(state, 'ROOT', self.root), contextlib.closing(state.connect()) as db:
            tracker = StatusTracker()
            scan_status(db, [pane], tracker)
            self.assertEqual(state.pane_status(db, pane), 'idle')
            with path.open('a') as stream:
                stream.write('{"status":"working","timestamp":"2026-10-04T12:00:02Z"}\n')
            scan_status(db, [pane], tracker)
            self.assertEqual(state.pane_status(db, pane), 'working')
            db.execute('INSERT INTO session_events VALUES(?,?,?,?)', ('custom', 'custom-session', 'waiting', 9999999999))
            db.commit()
            scan_status(db, [pane], tracker)
            self.assertEqual(state.pane_status(db, pane), 'waiting')

    def test_custom_hook_matches_interpreter_agent_in_tmux(self):
        self.configure({'agents': [dict(name='custom', executables=['custom'], scripts=['*/custom/cli.js'])]})
        records = {10: dict(pid=10, parent=1, name='zsh'), 11: dict(pid=11, parent=10, name='node')}
        with patch.object(state, 'ROOT', self.root), \
             patch.dict(hooks.os.environ, {'TMUX_PANE': '%1', 'TMUX': '/test,1,0'}), \
             patch.object(hooks.sys, 'stdin', io.StringIO('{}')), \
             patch.object(hooks, 'tmux', return_value=SimpleNamespace(stdout='10')), \
             patch.object(processes, 'inventory', return_value=records), \
             patch.object(processes, 'system', return_value=SimpleNamespace(argv=lambda pid: ['node', '/opt/custom/cli.js'])), \
             contextlib.redirect_stdout(io.StringIO()):
            hooks.hook('custom', 'Stop')
            with contextlib.closing(state.connect()) as db:
                event = db.execute('SELECT agent,status FROM events').fetchone()
                self.assertEqual(tuple(event), ('custom', 'done'))
