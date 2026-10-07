from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maixy import state
from maixy.status import observe


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = patch.object(state, 'ROOT', Path(self.directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.db = state.connect()
        self.addCleanup(self.db.close)
        self.pane = dict(identity='test:%1:10', socket='test', pane_id='%1', pane_pid='10', agent='codex')

    def test_removing_an_agent_does_not_move_other_keys(self):
        other = dict(self.pane, identity='test:%2:20', pane_id='%2', pane_pid='20')
        panes = state.assign_slots(self.db, [self.pane, other])
        self.assertEqual([pane['position'] for pane in panes], [0, 1])
        self.assertEqual(state.assign_slots(self.db, [other])[0]['position'], 1)
        new = dict(self.pane, identity='test:%3:30', pane_id='%3', pane_pid='30')
        panes = state.assign_slots(self.db, [other, new])
        self.assertEqual([pane['pane_id'] for pane in panes], ['%3', '%2'])

    def test_slots_survive_reopening_the_database(self):
        state.assign_slots(self.db, [self.pane])
        self.db.close()
        self.db = state.connect()
        self.addCleanup(self.db.close)
        self.assertEqual(state.assign_slots(self.db, [self.pane])[0]['position'], 0)

    def test_new_pane_owner_does_not_inherit_old_completion(self):
        observe(self.db, self.pane, 'working', 1)
        observe(self.db, self.pane, 'idle', 2)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
        replacement = dict(self.pane, pane_pid='99')
        self.assertEqual(state.pane_status(self.db, replacement), 'idle')

