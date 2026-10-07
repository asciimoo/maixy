from pathlib import Path
import contextlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from maixy import discovery, processes, hosts
from maixy.agents import codex
from maixy.agents.claude import Claude


def record(pid, parent, name, tty='', started='Sun Oct 4 12:00:00 2026'):
    return dict(pid=pid, parent=parent, name=name, tty=tty, started=started)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(Path, 'home', return_value=self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def discover(self, records, panes=(), threads=(), details=None, session=None):
        details = details or {pid: dict(cwd='/work/project', logs=[]) for pid in records}
        with patch.object(processes, 'inventory', return_value=records), \
             patch.object(processes, 'files', return_value=details), \
             patch.object(discovery.tmux, 'discover', return_value=list(panes)), \
             patch.object(codex, 'codex_threads', return_value=list(threads)), \
             patch.object(hosts, 'terminal_tabs', return_value={}), \
             patch.object(discovery.editor, 'bridges', return_value=[]):
            return discovery.discover('/socket', session)

    def test_standalone_terminals_are_found_without_tmux(self):
        records = {1: record(1, 0, '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal'),
                   10: record(10, 1, 'zsh', '/dev/ttys1'),
                   11: record(11, 10, 'claude', '/dev/ttys1'),
                   20: record(20, 1, 'zsh', '/dev/ttys2'),
                   21: record(21, 20, 'codex', '/dev/ttys2')}
        result = self.discover(records)
        self.assertEqual({p['agent_pid'] for p in result}, {11, 21})
        self.assertEqual({p['kind'] for p in result}, {'process'})
        self.assertEqual({p['host'] for p in result}, {'Terminal'})

    def test_tmux_processes_and_workers_are_not_duplicated(self):
        records = {1: record(1, 0, 'tmux: server'), 10: record(10, 1, 'zsh', '/dev/ttys1'),
                   11: record(11, 10, 'claude', '/dev/ttys1'), 12: record(12, 11, 'claude')}
        pane = dict(agent='claude', agent_pid=11)
        self.assertEqual(self.discover(records, [pane]), [pane])
        self.assertEqual(self.discover(records), [])

    def test_editor_server_requires_an_open_root_session_not_saved_history(self):
        records = {1: record(1, 0, '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'),
                   10: record(10, 1, '/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper'),
                   11: record(11, 10, 'codex'), 12: record(12, 11, 'codex')}
        threads = [dict(id='live', name='Live task', cwd='/work/project', rollout_path='/logs/live.jsonl', source='vscode'),
                   dict(id='old', name='Old task', cwd='/work/project', rollout_path='/logs/old.jsonl', source='vscode')]
        details = {pid: dict(cwd='/work/project', logs=['/logs/live.jsonl']) for pid in records}
        result = self.discover(records, threads=threads, details=details)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['thread_id'], 'live')
        self.assertEqual(result[0]['host'], 'VS Code')
        self.assertEqual(result[0]['host_pid'], 1)

    def test_shared_codex_daemon_does_not_repeat_a_tmux_thread(self):
        records = {11: record(11, 1, 'codex', '/dev/ttys1'), 20: record(20, 1, 'codex')}
        thread = dict(id='live', name='Live task', cwd='/work/project', rollout_path='/logs/live.jsonl', source='vscode')
        pane = dict(agent='codex', agent_pid=11, pane_title='Live task | user', pane_current_path='/work/project')
        details = {pid: dict(cwd='/work/project', logs=['/logs/live.jsonl']) for pid in records}
        result = self.discover(records, [pane], [thread], details)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['thread_id'], 'live')

    def test_explicit_tmux_session_filter_excludes_external_agents(self):
        records = {11: record(11, 1, 'claude', '/dev/ttys1')}
        self.assertEqual(self.discover(records, session='work'), [])

    def test_process_start_time_changes_identity_after_pid_reuse(self):
        before = self.discover({11: record(11, 1, 'claude', '/dev/ttys1')})[0]
        after = self.discover({11: record(11, 1, 'claude', '/dev/ttys1', 'different')})[0]
        self.assertNotEqual(before['identity'], after['identity'])

    def test_subagent_threads_are_excluded_from_database(self):
        import sqlite3
        directory = self.root / '.codex'
        directory.mkdir()
        import contextlib
        with contextlib.closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id,name,rollout_path,cwd,source,archived)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,0)', [
                ('root', 'Root', '/root.jsonl', '/work', 'vscode'),
                ('child', 'Child', '/child.jsonl', '/work', '{"subagent":{}}')])
            db.commit()
        self.assertEqual([r['id'] for r in codex.codex_threads()], ['root'])

    def test_detached_exec_worker_does_not_occupy_a_key(self):
        directory = self.root / '.codex'
        directory.mkdir()
        with contextlib.closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id,name,rollout_path,cwd,source,archived)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,0)', [
                ('exec', 'Background task', '/exec.jsonl', '/work', 'exec'),
                ('root', 'Editor task', '/root.jsonl', '/work', 'vscode'),
                ('cli', 'Terminal task', '/cli.jsonl', '/work', 'cli')])
            db.commit()
        threads = codex.codex_threads()
        self.assertEqual({t['id'] for t in threads}, {'root', 'cli'})
        records = {10: record(10, 1, 'bash'), 11: record(11, 10, 'codex'),
                   20: record(20, 1, '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'),
                   21: record(21, 20, 'codex')}
        details = {11: dict(cwd='/work', logs=['/exec.jsonl']),
                   21: dict(cwd='/work', logs=['/root.jsonl'])}
        result = self.discover(records, threads=threads, details=details)
        self.assertEqual([(p['agent_pid'], p['thread_id']) for p in result], [(21, 'root')])

    def test_exec_thread_is_excluded_even_when_editor_server_holds_it_open(self):
        directory = self.root / '.codex'
        directory.mkdir()
        with contextlib.closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id,name,rollout_path,cwd,source,archived)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,0)', [
                ('exec', 'Background task', '/exec.jsonl', '/work', 'exec'),
                ('root', 'Editor task', '/root.jsonl', '/work', 'vscode')])
            db.commit()
        records = {10: record(10, 1, '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'),
                   11: record(11, 10, 'codex')}
        details = {11: dict(cwd='/work', logs=['/exec.jsonl', '/root.jsonl'])}
        result = self.discover(records, threads=codex.codex_threads(), details=details)
        self.assertEqual([p['thread_id'] for p in result], ['root'])

    def test_codex_status_links_only_own_open_children_and_descendants(self):
        directory = self.root / '.codex'
        directory.mkdir()
        parent = self.root / 'parent.jsonl'
        def source(parent_id):
            return json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': parent_id}}})
        with contextlib.closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id,rollout_path,source,archived)')
            db.execute('CREATE TABLE thread_spawn_edges(child_thread_id,status)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?)', [
                ('parent', str(parent), 'cli', 0),
                ('child', '/child.jsonl', source('parent'), 0),
                ('grandchild', '/grandchild.jsonl', source('child'), 0),
                ('other', '/other.jsonl', source('unrelated'), 0),
                ('closed', '/closed.jsonl', source('parent'), 0),
                ('archived', '/archived.jsonl', source('parent'), 1),
                ('guardian', '/guardian.jsonl', '{"subagent":{"other":"guardian"}}', 0),
                ('review', '/review.jsonl', '{"subagent":"review"}', 0),
            ])
            db.execute("INSERT INTO thread_spawn_edges VALUES('closed','closed')")
            db.commit()
        paths = codex.Codex().subagent_paths({}, parent)
        self.assertEqual(set(paths), {Path('/child.jsonl'), Path('/grandchild.jsonl')})

    def test_claude_status_links_children_only_within_parent_session(self):
        parent = self.root / 'parent.jsonl'
        folder = self.root / 'parent/subagents'
        folder.mkdir(parents=True)
        child = folder / 'agent-child.jsonl'
        child.touch()
        (folder / 'unrelated.txt').touch()
        other = self.root / 'other/subagents'
        other.mkdir(parents=True)
        (other / 'agent-other.jsonl').touch()
        self.assertEqual(Claude().subagent_paths({}, parent), [child])

    def test_root_login_intermediary_preserves_terminal_host(self):
        records = {1: record(1, 0, '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal'),
                   2: dict(record(2, 1, 'login'), uid=0),
                   10: record(10, 2, 'zsh', '/dev/ttys1'),
                   11: record(11, 10, 'claude', '/dev/ttys1'),
                   12: dict(record(12, 0, 'claude', '/dev/ttys2'), uid=0)}
        with patch.object(discovery.os, 'getuid', return_value=502):
            result = self.discover(records)
        self.assertEqual([p['agent_pid'] for p in result], [11])
        self.assertEqual(result[0]['host'], 'Terminal')
