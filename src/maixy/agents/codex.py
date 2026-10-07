"""Codex CLI and live root threads owned by app servers."""
import contextlib
import json
from pathlib import Path
import re
import sqlite3

from .base import Agent


def codex_threads():
    path = Path.home() / '.codex/state_5.sqlite'
    if not path.exists():
        return []
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT id,name,rollout_path,cwd,source FROM threads WHERE archived=0').fetchall()
        # Noninteractive exec workers can outlive their launching agent and
        # lose its process ancestry. Their logs remain open, but they do not
        # represent a conversation the user can select.
        return [dict(row) for row in rows
                if row['source'] != 'exec' and 'subagent' not in (row['source'] or '')]
    except sqlite3.Error:
        return []


def match_thread(pane, threads):
    names = {part.strip() for part in pane['pane_title'].split('|')}
    matches = [t for t in threads if t['cwd'] == pane['pane_current_path'] and t['name'] in names]
    return matches[0] if len(matches) == 1 else None


class Codex(Agent):
    hook_events = Agent.hook_events + ('Interrupt',)

    def __init__(self):
        super().__init__('codex', ('codex',), ('*/@openai/codex/*',))

    def prepare(self):
        return dict(threads=codex_threads(), used=set())

    def enrich(self, pane, context):
        thread = match_thread(pane, context['threads'])
        if thread:
            pane.update(session_path=thread['rollout_path'], thread_id=thread['id'])
            context['used'].add(thread['id'])

    def discover(self, pane, record, files, context):
        live_paths = set(files['logs'])
        opened = [t for t in context['threads'] if t['rollout_path'] in live_paths and t['id'] not in context['used']]
        named = match_thread(pane, context['threads'])
        if named and named['id'] not in context['used']:
            opened = [named]
        if record['tty'] and len(opened) != 1:
            return [pane]
        result = []
        for thread in opened:
            item = dict(pane, session_path=thread['rollout_path'], thread_id=thread['id'],
                        pane_title=thread['name'] or Path(thread['cwd']).name,
                        pane_current_path=thread['cwd'])
            if not record['tty']:
                item.update(pane_id='thread:' + thread['id'],
                            identity='local:thread:' + thread['id'] + ':' + pane['pane_pid'])
            result.append(item)
            context['used'].add(thread['id'])
        return result

    def resolve(self, pane):
        path = super().resolve(pane)
        if path:
            return path
        matches = [Path(t['rollout_path']) for t in codex_threads()
                   if t['cwd'] == pane['pane_current_path'] and
                   t['name'] in {p.strip() for p in pane['pane_title'].split('|')} and
                   Path(t['rollout_path']).exists()]
        return matches[0] if len(matches) == 1 else None

    def session_id(self, pane):
        return pane.get('thread_id')

    def subagent_paths(self, pane, path):
        database = Path.home() / '.codex/state_5.sqlite'
        if not database.exists():
            return []
        with contextlib.closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT id,rollout_path,source FROM threads WHERE archived=0').fetchall()
            try:
                closed = {r[0] for r in db.execute("SELECT child_thread_id FROM thread_spawn_edges WHERE status='closed'")}
            except sqlite3.OperationalError:
                closed = set()  # Older Codex databases have no spawn-edge table.
        root = pane.get('thread_id') or next((r['id'] for r in rows if r['rollout_path'] == str(path)), None)
        if not root:
            return []
        children = {}
        for row in rows:
            try:
                source = json.loads(row['source'])
                parent = source['subagent']['thread_spawn']['parent_thread_id']
            except (ValueError, TypeError, KeyError):
                continue
            if row['id'] not in closed:
                children.setdefault(parent, []).append(row)
        pending, seen, paths = [root], {root}, []
        while pending:
            for child in children.get(pending.pop(), ()):
                if child['id'] not in seen:
                    seen.add(child['id'])
                    pending.append(child['id'])
                    paths.append(Path(child['rollout_path']))
        return paths

    def display_status(self, text, title=''):
        lines = [line.strip() for line in text.splitlines()[-18:]]
        # The live status header is customizable and may have an animated
        # bullet. Match its elapsed timer and interrupt affordance instead.
        if any(re.match(r'^\S.*\(\d+[hms](?:\s+\d+[hms])*\s*[•·]\s*(?:esc|ctrl.c) to interrupt(?:\)|$)', line, re.I)
               for line in lines):
            return 'working'
        dialog_controls = any(re.match(r'^›\s*\d+[.)]\s+\S', line) or
                              re.search(r'(?:enter to confirm|esc to cancel)', line, re.I) for line in lines)
        if dialog_controls and any(re.match(r'^(?:Would you like to (?:run|send input|grant|make)|'
                                            r'Do you want to (?:run|proceed|approve)|'
                                            r'Approve this|Allow .*\?)', line, re.I) for line in lines):
            return 'waiting'
        if re.search(r'\[\s*!\s*\]\s*Action Required', title):
            return 'waiting'
        if any(line.startswith('›') for line in lines):
            if any(re.match(r'^[■▪]\s*(?:Conversation|Turn) interrupted', line, re.I) for line in lines):
                return 'interrupted'
            return 'idle'
        return None

    def event_status(self, obj):
        if obj.get('type') == 'event_msg':
            return {'task_started': 'working', 'task_complete': 'done',
                    'task_cancelled': 'idle', 'turn_aborted': 'idle'}.get(obj.get('payload', {}).get('type'))
        return None

    def hook_path(self):
        return Path.home() / '.codex/hooks.json'

    def hook_notice(self):
        return 'Codex: review and trust Maixy hooks using /hooks.'
