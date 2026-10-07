import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maixy import state
from maixy.agents import registry
from maixy.status import (
    SessionTail, StatusTracker, display_status, event_status, event_time, observe,
    scan_status,
)

CODEX_APPROVAL = 'Would you like to run the following command?\n› 1. Yes\n  2. No\nPress enter to confirm or esc to cancel'


class DisplayTests(unittest.TestCase):
    def test_working_takes_precedence_over_codex_prompt(self):
        self.assertEqual(display_status('codex', 'Working (12s • esc to interrupt)\n› Ask Codex'), 'working')

    def test_codex_animation_is_working_despite_stale_approval_hints(self):
        for prefix in ('', '• ', '◦ ', '· ', '⠋ '):
            with self.subTest(prefix=prefix):
                self.assertEqual(display_status('codex',
                    CODEX_APPROVAL + '\n' + prefix + 'Working (12s • esc to interrupt)\n› Ask Codex',
                    '[ ! ] Action Required'), 'working')

    def test_codex_approval_prose_requires_dialog_controls(self):
        self.assertIsNone(display_status('codex', 'Would you like to run this command?'))
        self.assertEqual(display_status('codex', 'Would you like to run this command?\n› Ask Codex'), 'idle')

    def test_codex_custom_activity_header_is_working(self):
        self.assertEqual(display_status('codex',
            '◦ Mapping the app structure (1h 02m 03s • esc to interrupt)\n› Ask Codex',
            '[ ! ] Action Required'), 'working')

    def test_waiting_and_interrupted(self):
        self.assertEqual(display_status('codex', CODEX_APPROVAL), 'waiting')
        self.assertEqual(display_status('codex', '■ Conversation interrupted\n› Ask Codex'), 'interrupted')
        self.assertEqual(display_status('claude', 'Do you want to proceed?\n❯'), 'waiting')

    def test_claude_spinner_and_ready(self):
        self.assertEqual(display_status('claude', '✻ Pondering… (23s · ↓ 123 tokens)\n❯'), 'working')
        self.assertEqual(display_status('claude', '❯\n⏵⏵ auto mode on'), 'idle')

    def test_claude_question_dialog_overrides_spinner(self):
        screen = '✻ Pondering… (23s · ↓ 123 tokens)\nWhich option?\n❯ 1. First\n  2. Second\nEnter to select · ↑/↓ to navigate · Esc to cancel'
        self.assertEqual(display_status('claude', screen), 'waiting')
        self.assertIsNone(display_status('claude', 'Which option?\n1. First\n2. Second'))

    def test_conversation_text_does_not_imply_status(self):
        self.assertIsNone(display_status('codex', 'The word Working appears in this sentence.'))
        self.assertIsNone(display_status('claude', 'I was Thinking… about tokens yesterday.'))


class EventTests(unittest.TestCase):
    def test_codex_lifecycle(self):
        for event, expected in [('task_started', 'working'), ('task_complete', 'done'), ('turn_aborted', 'idle')]:
            with self.subTest(event=event):
                self.assertEqual(event_status('codex', {'type': 'event_msg', 'payload': {'type': event}}), expected)

    def test_tool_activity_is_not_a_finished_claude_turn(self):
        self.assertEqual(event_status('claude', {'type': 'assistant', 'message': {'stop_reason': 'tool_use'}}), 'working')
        self.assertEqual(event_status('claude', {'type': 'user', 'message': {'content': [{'type': 'tool_result'}]}}), 'working')
        self.assertEqual(event_status('claude', {'type': 'user', 'message': {'content': [{'type': 'text'}]}}), 'working')
        self.assertEqual(event_status('claude', {'type': 'assistant', 'message': {'stop_reason': 'end_turn'}}), 'done')

    def test_invalid_time_is_ignored(self):
        self.assertIsNone(event_time({'timestamp': 'invalid'}))
        self.assertEqual(event_time({'timestamp': '1970-01-01T00:00:01Z'}), 1)

    def test_claude_handback_requires_explicit_turn_ending_result(self):
        for ends_turn, error, expected in ((True, False, 'done'), (False, False, 'working'),
                                          (None, False, 'working'), (True, True, 'working')):
            with self.subTest(ends_turn=ends_turn, error=error):
                self.assertEqual(event_status('claude', {
                    'type': 'user', 'agentId': 'child', 'toolEndsTurn': ends_turn,
                    'message': {'content': [{'type': 'tool_result', 'is_error': error}]},
                    'toolUseResult': {'success': not error},
                }), expected)
        self.assertEqual(event_status('claude', {
            'type': 'assistant', 'message': {'stop_reason': None, 'content': [
                {'type': 'tool_use', 'name': 'SubagentHandback'},
            ]},
        }), 'working')

    def test_claude_assistant_blocks_resume_work_without_user_prompt(self):
        for block in ('thinking', 'tool_use', 'text'):
            with self.subTest(block=block):
                self.assertEqual(event_status('claude', {
                    'type': 'assistant', 'message': {'stop_reason': None, 'content': [{'type': block}]},
                }), 'working')
                self.assertEqual(event_status('claude', {
                    'type': 'assistant', 'message': {'stop_reason': 'end_turn', 'content': [{'type': block}]},
                }), 'done')

    def test_claude_local_commands_do_not_start_assistant_turns(self):
        command = '<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>opus</command-args>'
        output = '<local-command-stdout>Set model to opus</local-command-stdout>'
        for text, expected in ((command, None), (output, 'idle')):
            for content in (text, [{'type': 'text', 'text': text}]):
                with self.subTest(content=content):
                    self.assertEqual(event_status('claude', {'type': 'user', 'message': {'content': content}}), expected)
        self.assertEqual(event_status('claude', {'type': 'system', 'subtype': 'local_command'}), 'idle')
        self.assertIsNone(event_status('claude', {
            'type': 'user', 'isMeta': True,
            'message': {'content': '<local-command-caveat>Local command</local-command-caveat>'},
        }))

    def test_claude_prompt_discussing_commands_still_starts_work(self):
        self.assertEqual(event_status('claude', {
            'type': 'user', 'message': {'content': 'Explain <command-name>/model</command-name>'},
        }), 'working')

    def test_claude_question_tool_wait_and_answer(self):
        self.assertEqual(event_status('claude', {
            'type': 'assistant', 'message': {'stop_reason': 'tool_use', 'content': [
                {'type': 'text', 'text': 'Choose an option'},
                {'type': 'tool_use', 'name': 'AskUserQuestion', 'id': 'question-1', 'input': {'questions': []}},
            ]},
        }), 'waiting')
        self.assertEqual(event_status('claude', {
            'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'question-1'}]},
            'toolUseResult': {'questions': [], 'answers': {}, 'annotations': {}},
        }), 'working')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(state, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        home = patch('maixy.agents.codex.Path.home', return_value=self.root)
        home.start()
        self.addCleanup(home.stop)
        self.db = state.connect()
        self.addCleanup(self.db.close)
        self.pane = dict(socket='test', pane_id='%1', pane_pid='10', agent='codex', identity='test:%1:10')
        self.path = self.root / 'session.jsonl'

    def append(self, kind, seconds):
        obj = {'type': 'event_msg', 'timestamp': '2026-10-04T12:00:{:02d}Z'.format(seconds), 'payload': {'type': kind}}
        with self.path.open('a') as stream:
            stream.write(json.dumps(obj) + '\n')

    def append_claude(self, kind, seconds, **fields):
        obj = dict(type=kind, timestamp='2026-10-04T12:00:{:02d}Z'.format(seconds), **fields)
        with self.path.open('a') as stream:
            stream.write(json.dumps(obj) + '\n')

    def child_event(self, path, agent, status, seconds):
        obj = {'timestamp': '2026-10-04T12:00:{:02d}Z'.format(seconds)}
        if agent == 'codex':
            obj.update(type='event_msg', payload={'type': {'working': 'task_started', 'done': 'task_complete'}[status]})
        else:
            obj.update(type='user' if status == 'working' else 'assistant',
                       message={'content': 'Task'} if status == 'working' else {'stop_reason': 'end_turn'})
        with path.open('a') as stream:
            stream.write(json.dumps(obj) + '\n')

    def test_parent_stays_working_until_all_children_finish(self):
        for agent in ('codex', 'claude'):
            with self.subTest(agent=agent):
                self.db.execute('DELETE FROM events')
                self.db.execute('DELETE FROM observations')
                self.path.write_text('')
                self.pane.update(agent=agent, kind='process', session_path=str(self.path), pane_title='Test')
                child_paths = [self.root / (agent + str(i) + '.jsonl') for i in range(2)]
                self.child_event(self.path, agent, 'done', 1)
                for path in child_paths:
                    self.child_event(path, agent, 'working', 2)
                tracker = StatusTracker()
                with patch.object(registry().get(agent), 'subagent_paths', return_value=child_paths), \
                     patch('maixy.status.host_screen', return_value='› Ask Codex' if agent == 'codex' else '❯'):
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'working')
                    self.assertEqual(self.pane['background_count'], 2)
                    self.child_event(self.path, agent, 'working', 3)
                    self.child_event(self.path, agent, 'done', 4)
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'working')
                    self.child_event(child_paths[0], agent, 'done', 5)
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'working')
                    self.assertEqual(self.pane['background_count'], 1)
                    tracker = StatusTracker()  # Reload while another child is still busy.
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'working')
                    self.assertEqual(self.pane['background_count'], 1)
                    self.child_event(child_paths[1], agent, 'done', 6)
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'done')
                    self.assertEqual(self.pane['background_count'], 0)
                    self.db.execute('UPDATE events SET acknowledged=updated')
                    self.db.commit()
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
                    scan_status(self.db, [self.pane], StatusTracker())
                    self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_completed_historical_children_do_not_create_completion_at_startup(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        child = self.root / 'child.jsonl'
        self.child_event(self.path, 'codex', 'done', 2)
        self.child_event(child, 'codex', 'done', 1)
        with patch.object(registry().get('codex'), 'subagent_paths', return_value=[child]), \
             patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], StatusTracker())
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_claude_child_handback_finishes_parent_and_supports_resume(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        child_dir = self.path.parent / self.path.stem / 'subagents'
        child_dir.mkdir(parents=True)
        child = child_dir / 'agent-child.jsonl'
        self.child_event(self.path, 'claude', 'done', 1)
        self.child_event(child, 'claude', 'working', 2)
        handback = {'type': 'user', 'agentId': 'child', 'toolEndsTurn': True,
                    'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'handback'}]},
                    'toolUseResult': {'success': True}}
        for seconds, recover_after_reload in ((3, False), (5, True)):
            with self.subTest(recover_after_reload=recover_after_reload):
                tracker = StatusTracker()
                with patch('maixy.status.host_screen', return_value='❯'):
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'working')
                    with child.open('a') as stream:
                        stream.write(json.dumps(dict(handback, timestamp='2026-10-04T12:00:{:02d}Z'.format(seconds))) + '\n')
                    if recover_after_reload:
                        tracker = StatusTracker()
                    scan_status(self.db, [self.pane], tracker)
                    self.assertEqual(state.pane_status(self.db, self.pane), 'done')
                    scan_status(self.db, [self.pane], StatusTracker())
                    self.assertEqual(state.pane_status(self.db, self.pane), 'done')
                    self.db.execute('UPDATE events SET acknowledged=updated')
                    self.db.commit()
                    scan_status(self.db, [self.pane], StatusTracker())
                    self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
                # Resume the same child in a new turn; its old handback cannot
                # prevent it from keeping the parent working again.
                if not recover_after_reload:
                    self.child_event(child, 'claude', 'working', 4)

    def test_unreadable_child_does_not_claim_finished_or_ready(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        self.child_event(self.path, 'codex', 'done', 1)
        with patch.object(registry().get('codex'), 'subagent_paths', return_value=[self.root / 'missing.jsonl']), \
             patch('maixy.status.host_screen', return_value='› Ask Codex'):
            scan_status(self.db, [self.pane], StatusTracker())
        self.assertEqual(state.pane_status(self.db, self.pane), 'unknown')

    def test_reload_does_not_recover_completion_from_unrelated_activity(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        self.child_event(self.path, 'codex', 'done', 3)
        older = event_time({'timestamp': '2026-10-04T12:00:02Z'})
        newer = event_time({'timestamp': '2026-10-04T12:00:04Z'})
        for owner, agent, session, stamp in (
                ('previous-owner', 'codex', str(self.path), older),
                ('10', 'claude', str(self.path), older),
                ('10', 'codex', 'previous-session', older),
                ('10', 'codex', str(self.path), newer)):
            with self.subTest(owner=owner, agent=agent, session=session, stamp=stamp):
                self.db.execute('DELETE FROM events')
                self.db.execute('INSERT INTO events(socket,pane,status,updated,agent,session,owner) VALUES(?,?,?,?,?,?,?)',
                                ('test', '%1', 'working', stamp, agent, session, owner))
                self.db.commit()
                with patch('maixy.status.host_screen', return_value=None):
                    scan_status(self.db, [self.pane], StatusTracker())
                self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_parent_question_has_priority_over_busy_children(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        child = self.root / 'child.jsonl'
        self.child_event(child, 'claude', 'working', 1)
        self.append_claude('assistant', 2, message={'content': [{'type': 'tool_use', 'name': 'AskUserQuestion'}]})
        with patch.object(registry().get('claude'), 'subagent_paths', return_value=[child]), \
             patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], StatusTracker())
        self.assertEqual(state.pane_status(self.db, self.pane), 'waiting')
        self.assertEqual(self.pane['background_count'], 1)

    def test_claude_parent_continuation_outlasts_child_completion(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        child = self.root / 'child.jsonl'
        self.child_event(child, 'claude', 'working', 1)
        self.append_claude('assistant', 2, message={'stop_reason': 'end_turn'})
        tracker = StatusTracker()
        with patch.object(registry().get('claude'), 'subagent_paths', return_value=[child]), \
             patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            # The parent resumes from a background notification, without a
            # new user text prompt. Its last reply no longer means it is done.
            self.append_claude('user', 3, isMeta=True, message={'content': 'Background notification'})
            self.append_claude('assistant', 4, message={'stop_reason': 'tool_use', 'content': [
                {'type': 'tool_use', 'name': 'Bash', 'id': 'command'},
            ]})
            scan_status(self.db, [self.pane], tracker)
            self.child_event(child, 'claude', 'done', 5)
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append_claude('user', 6, message={'content': [{'type': 'tool_result', 'tool_use_id': 'command'}]})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            tracker = StatusTracker()
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append_claude('assistant', 7, message={'stop_reason': None, 'content': [{'type': 'thinking'}]})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append_claude('assistant', 8, message={'stop_reason': 'end_turn', 'content': [{'type': 'text'}]})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            self.db.execute('UPDATE events SET acknowledged=updated')
            self.db.commit()
            scan_status(self.db, [self.pane], StatusTracker())
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_stop_hook_and_idle_screen_do_not_finish_busy_children(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test', thread_id='parent')
        self.child_event(self.path, 'codex', 'done', 1)
        child = self.root / 'child.jsonl'
        self.child_event(child, 'codex', 'working', 2)
        stamp = event_time({'timestamp': '2026-10-04T12:00:03Z'})
        self.db.execute('INSERT INTO session_events VALUES(?,?,?,?)', ('codex', 'parent', 'done', stamp))
        self.db.commit()
        tracker = StatusTracker()
        with patch.object(registry().get('codex'), 'subagent_paths', return_value=[child]), \
             patch('maixy.status.host_screen', return_value='› Ask Codex'):
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.child_event(child, 'codex', 'done', 4)
            scan_status(self.db, [self.pane], tracker)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')

    def test_closed_child_completion_is_not_backdated_or_repeated(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        child = self.root / 'child.jsonl'
        self.child_event(self.path, 'codex', 'done', 1)
        self.child_event(child, 'codex', 'working', 2)
        children = [child]
        tracker = StatusTracker()
        with patch.object(registry().get('codex'), 'subagent_paths', side_effect=lambda pane, path: list(children)), \
             patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], tracker)
            self.db.execute('UPDATE events SET acknowledged=updated')
            self.db.commit()
            children.clear()
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            self.db.execute('UPDATE events SET acknowledged=updated')
            self.db.commit()
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_claude_model_change_and_next_real_turn_without_screen(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        self.append_claude('assistant', 1, message={'stop_reason': 'end_turn'})
        tracker = StatusTracker()
        with patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
            self.append_claude('user', 2, message={'content': '<command-name>/model</command-name>'})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
            self.append_claude('user', 3, message={'content': '<local-command-stdout>Set model to opus</local-command-stdout>'})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
            self.append_claude('user', 4, message={'content': 'Implement the feature'})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append_claude('assistant', 5, message={'stop_reason': 'end_turn'})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            self.db.execute('UPDATE events SET acknowledged=updated')
            self.db.commit()
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_claude_local_command_baseline_clears_stale_working_after_reload(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        for previous in ('idle', 'working'):
            for kind, fields in (
                ('user', {'message': {'content': '<local-command-stdout>Set model to opus</local-command-stdout>'}}),
                ('system', {'subtype': 'local_command'}),
            ):
                with self.subTest(previous=previous, kind=kind):
                    self.db.execute('DELETE FROM observations')
                    observe(self.db, self.pane, previous)
                    self.db.execute("UPDATE events SET status='working'")
                    self.db.commit()
                    self.path.write_text('')
                    self.append_claude(kind, 1, **fields)
                    with patch('maixy.status.host_screen', return_value=None):
                        scan_status(self.db, [self.pane], StatusTracker())
                    self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_incremental_log_and_partial_record(self):
        self.append('task_started', 1)
        tail = SessionTail(self.path, 'codex')
        self.assertEqual(tail.read()[0][0], 'working')
        self.assertEqual(tail.read(), (None, False))
        record = json.dumps({'type': 'event_msg', 'timestamp': '2026-10-04T12:00:02Z', 'payload': {'type': 'task_complete'}})
        with self.path.open('a') as stream:
            stream.write(record[:20])
        self.assertEqual(tail.read(), (None, False))
        with self.path.open('a') as stream:
            stream.write(record[20:] + '\n')
        self.assertEqual(tail.read()[0][0], 'done')

    def test_truncated_log_reestablishes_baseline(self):
        self.append('task_complete', 1)
        tail = SessionTail(self.path, 'codex')
        tail.read()
        self.path.write_text('')
        self.assertEqual(tail.read(), (None, True))
        self.append('task_started', 2)
        self.assertEqual(tail.read()[0][0], 'working')

    def test_completion_acknowledgment_and_next_prompt(self):
        self.append('task_complete', 1)
        tracker = StatusTracker()
        tracker.resolve = lambda pane: self.path
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
        self.append('task_started', 2)
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'working')
        # A redraw with no new lifecycle event must not finish the prompt.
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'working')
        self.append('task_complete', 3)
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
        self.db.execute('UPDATE events SET acknowledged=updated')
        self.db.commit()
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
        self.append('task_started', 4)
        self.append('task_complete', 5)
        tracker.native(self.db, self.pane)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')

    def test_fallback_waits_for_stable_ready_screen(self):
        tracker = StatusTracker()
        tracker.fallback(self.db, self.pane, 'working')
        with patch('maixy.status.time.monotonic', return_value=1):
            tracker.fallback(self.db, self.pane, 'idle')
        self.assertEqual(state.pane_status(self.db, self.pane), 'working')
        with patch('maixy.status.time.monotonic', return_value=3):
            tracker.fallback(self.db, self.pane, 'idle')
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')

    def test_interruption_does_not_turn_green(self):
        observe(self.db, self.pane, 'working', 1)
        observe(self.db, self.pane, 'interrupted', 2)
        self.assertEqual(state.pane_status(self.db, self.pane), 'idle')

    def test_external_native_lifecycle_works_without_tmux_or_screen(self):
        self.pane.update(kind='process', session_path=str(self.path))
        self.append('task_started', 1)
        tracker = StatusTracker()
        with patch('maixy.status.host_screen', return_value=None), patch('maixy.status.tmux') as tmux:
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append('task_complete', 2)
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')
            self.db.execute('UPDATE events SET acknowledged=updated')
            self.db.commit()
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'idle')
            tmux.assert_not_called()

    def test_external_terminal_approval_is_orange(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        self.append('task_started', 1)
        tracker = StatusTracker()
        with patch('maixy.status.host_screen', return_value=CODEX_APPROVAL):
            with patch('maixy.status.time.monotonic', return_value=1):
                scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            with patch('maixy.status.time.monotonic', return_value=2):
                scan_status(self.db, [self.pane], tracker)
        self.assertEqual(state.pane_status(self.db, self.pane), 'waiting')

    def test_transient_codex_screen_wait_does_not_flash_orange(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Test')
        self.append('task_started', 1)
        tracker = StatusTracker()
        for tick, screen in ((1, CODEX_APPROVAL), (1.3, CODEX_APPROVAL),
                             (1.5, '• Working (12s • esc to interrupt)'),
                             (2, CODEX_APPROVAL), (2.3, CODEX_APPROVAL)):
            with patch('maixy.status.time.monotonic', return_value=tick), \
                 patch('maixy.status.host_screen', return_value=screen):
                scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
        scan_status(self.db, [], tracker)
        self.assertEqual(tracker.pending_wait, {})

    def test_claude_question_wait_and_resume_without_screen(self):
        self.pane.update(agent='claude', kind='process', session_path=str(self.path), pane_title='Test')
        self.append_claude('user', 1, message={'content': 'Implement the feature'})
        tracker = StatusTracker()
        with patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], tracker)
            self.append_claude('assistant', 2, message={'stop_reason': 'tool_use', 'content': [
                {'type': 'tool_use', 'id': 'question-1', 'name': 'AskUserQuestion', 'input': {'questions': []}},
            ]})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'waiting')
            scan_status(self.db, [self.pane], StatusTracker())
            self.assertEqual(state.pane_status(self.db, self.pane), 'waiting')
            self.append_claude('user', 3, message={'content': [{'type': 'tool_result', 'tool_use_id': 'question-1'}]},
                               toolUseResult={'questions': [], 'answers': {}})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'working')
            self.append_claude('assistant', 4, message={'stop_reason': 'end_turn'})
            scan_status(self.db, [self.pane], tracker)
            self.assertEqual(state.pane_status(self.db, self.pane), 'done')

    def test_unresolved_external_status_is_unknown(self):
        self.pane.update(kind='process', pane_title='Test', pane_current_path='/missing')
        with patch('maixy.status.host_screen', return_value=None), patch.object(StatusTracker, 'resolve', return_value=None):
            scan_status(self.db, [self.pane], StatusTracker())
        self.assertEqual(state.pane_status(self.db, self.pane), 'unknown')

    def test_existing_hook_wait_survives_native_baseline_without_screen(self):
        self.pane.update(kind='process', session_path=str(self.path), thread_id='outside')
        self.append('task_started', 1)
        stamp = event_time({'timestamp': '2026-10-04T12:00:02Z'})
        self.db.execute('INSERT INTO session_events VALUES(?,?,?,?)', ('codex', 'outside', 'waiting', stamp))
        self.db.commit()
        with patch('maixy.status.host_screen', return_value=None):
            scan_status(self.db, [self.pane], StatusTracker())
        self.assertEqual(state.pane_status(self.db, self.pane), 'waiting')

    def test_long_running_turn_is_resolved_beyond_initial_tail(self):
        self.pane.update(kind='process', session_path=str(self.path), pane_title='Long task')
        self.append('task_started', 1)
        with self.path.open('a') as stream:
            # No lifecycle event within the last megabyte, and a record spans
            # the chunk boundary. Conversation text is never kept as state.
            stream.write(json.dumps({'type': 'response_item', 'payload': 'x' * (2 * 1024 * 1024)}) + '\n')
        tracker = StatusTracker()
        with patch('maixy.status.host_screen', return_value='› Ask Codex'):
            scan_status(self.db, [self.pane], tracker)
            scan_status(self.db, [self.pane], tracker)
        self.assertEqual(state.pane_status(self.db, self.pane), 'working')
        self.append('task_complete', 2)
        with patch('maixy.status.host_screen', return_value='› Ask Codex'):
            scan_status(self.db, [self.pane], tracker)
        self.assertEqual(state.pane_status(self.db, self.pane), 'done')
