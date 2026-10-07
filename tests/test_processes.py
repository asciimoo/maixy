from types import SimpleNamespace
import unittest
from unittest.mock import patch

from maixy import processes
from maixy.systems import macos, posix


class ProcessTests(unittest.TestCase):
    def test_linux_and_macos_inventory_keep_ancestry_and_start_time(self):
        output = ('10 1 502 pts/2 Sun Oct 4 12:34:56 2026 codex\n'
                  '11 1 502 ttys003 Sun Oct 4 12:34:57 2026 /Applications/Visual Studio Code.app/Contents/MacOS/Electron\n'
                  '12 1 502 ? Sun Oct 4 12:34:58 2026 claude\n'
                  '13 1 0 ? Sun Oct 4 12:34:59 2026 codex\n')
        with patch.object(posix, 'run', return_value=SimpleNamespace(stdout=output)):
            records = processes.inventory()
        self.assertEqual(set(records), {10, 11, 12, 13})
        self.assertEqual(records[13]['uid'], 0)
        self.assertEqual(records[10]['tty'], '/dev/pts/2')
        self.assertEqual(records[12]['tty'], '')
        self.assertEqual(records[11]['name'], '/Applications/Visual Studio Code.app/Contents/MacOS/Electron')
        self.assertEqual(records[11]['started'], 'Sun Oct 4 12:34:57 2026')

    def test_npm_entrypoints_are_recognized_without_reading_environment(self):
        record = dict(pid=10, name='node')
        with patch.object(processes, 'system', return_value=macos), \
             patch.object(macos, 'run', return_value=SimpleNamespace(stdout='node /opt/node_modules/@anthropic-ai/claude-code/cli.js')) as run:
            self.assertEqual(processes.agent_name(record), 'claude')
        self.assertEqual(run.call_args.args[0], ['ps', '-p', '10', '-o', 'args='])

    def test_process_tree_normalizes_npm_agents_for_tmux_discovery(self):
        records = {10: dict(pid=10, parent=1, name='node', agent='codex')}
        children, names = processes.tree(records)
        self.assertEqual(children[1], [10])
        self.assertEqual(names[10], 'codex')
