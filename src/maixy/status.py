from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from .agents import registry
from .tmux import tmux
from .hosts import screen as host_screen


def display_status(agent, text, title=''):
    adapter = registry().get(agent)
    return adapter.display_status(text, title) if adapter else None


def observe(db, pane, status, now=None):
    if status is None:
        return
    now = time.time() if now is None else now
    key = (pane['socket'], pane['pane_id'])
    previous = db.execute('SELECT * FROM observations WHERE socket=? AND pane=?', key).fetchone()
    same_owner = previous is not None and previous['owner'] == pane['pane_pid']
    previous_status = previous['status'] if same_owner else None
    # Only a live transition from activity to the ready prompt is a completion.
    # Refreshing an unchanged ready screen must not restore an acknowledged green.
    if status == previous_status:
        return
    state = 'done' if status == 'idle' and previous_status in ('working', 'waiting') else status
    if state == 'interrupted':
        state = 'idle'
    with db:
        db.execute('''INSERT INTO observations VALUES(?,?,?,?)
          ON CONFLICT(socket,pane) DO UPDATE SET owner=excluded.owner,status=excluded.status''',
          (*key, pane['pane_pid'], status))
        # Initial ready detection preserves a valid optional hook completion.
        current = db.execute('SELECT * FROM events WHERE socket=? AND pane=?', key).fetchone()
        if previous_status is None and state == 'idle' and current and current['owner'] == pane['pane_pid']:
            return
        db.execute('''INSERT INTO events(socket,pane,status,updated,agent,session,owner)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(socket,pane) DO UPDATE SET
          status=excluded.status,updated=excluded.updated,agent=excluded.agent,
          session=excluded.session,owner=excluded.owner''',
          (*key, state, now, pane['agent'], 'tmux-display', pane['pane_pid']))


def event_status(agent, obj):
    adapter = registry().get(agent)
    return adapter.event_status(obj) if adapter else None


def event_time(obj):
    try:
        return datetime.fromisoformat(obj['timestamp'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


class SessionTail:
    def __init__(self, path, agent):
        self.path, self.agent = Path(path), agent
        self.offset, self.remainder, self.last, self.initialized = 0, b'', None, False

    @staticmethod
    def signal(line, agent):
        try:
            obj = json.loads(line)
            status, stamp = event_status(agent, obj), event_time(obj)
            return (status, stamp) if status and stamp else None
        except (ValueError, TypeError, AttributeError):
            return None

    def baseline(self, end):
        # A long running turn can produce megabytes after task_started. Walk
        # backwards to the latest lifecycle event with bounded memory instead
        # of treating a missing event in the tail as an unresolved session.
        suffix = b''
        with self.path.open('rb') as stream:
            while end:
                start = max(0, end - 1024 * 1024)
                stream.seek(start)
                lines = (stream.read(end - start) + suffix).split(b'\n')
                suffix = lines.pop(0) if start else b''
                for line in reversed(lines):
                    event = self.signal(line, self.agent)
                    if event:
                        return event
                end = start
        return None

    def read(self):
        size = self.path.stat().st_size
        initial = not self.initialized or size < self.offset
        if initial:
            self.offset, self.remainder, self.last = max(0, size - 1024 * 1024), b'', None
        if size == self.offset and not initial:
            return None, False
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read()
            if initial and self.offset:
                data = data.split(b'\n', 1)[-1]
            self.offset = stream.tell()
        lines = (self.remainder + data).split(b'\n')
        self.remainder = lines.pop()
        latest = self.last
        for line in lines:
            event = self.signal(line, self.agent)
            if event:
                latest = event
        if initial and latest is None:
            latest = self.baseline(self.offset - len(self.remainder))
        changed = latest != self.last
        self.last, self.initialized = latest, True
        return (latest if changed or initial else None), initial


class StatusTracker:
    def __init__(self):
        self.tails = {}
        self.pending_idle = {}
        self.pending_wait = {}
        self.child_tails = {}
        self.signals = {}
        self.active_children = {}

    def stable_screen(self, pane, status):
        identity = pane['identity']
        if pane['agent'] == 'codex' and status == 'waiting':
            started = self.pending_wait.setdefault(identity, time.monotonic())
            # Titles and captured screens can disagree during a TUI redraw or
            # briefly show a request that automatic review resolves itself.
            if time.monotonic() - started < .75:
                return None
        else:
            self.pending_wait.pop(identity, None)
        return status

    def resolve(self, pane):
        adapter = registry().get(pane['agent'])
        return adapter.resolve(pane) if adapter else None

    def native(self, db, pane):
        path = self.resolve(pane)
        if path is None:
            return False
        identity = pane['identity']
        tail = self.tails.get(identity)
        if tail is None or tail.path != path:
            tail = self.tails[identity] = SessionTail(path, pane['agent'])
            self.child_tails.pop(identity, None)
            self.signals.pop(identity, None)
        event, initial = tail.read()
        if tail.last is None:
            return False
        adapter = registry().get(pane['agent'])
        children = self.child_tails.setdefault(identity, {})
        unresolved = False
        try:
            paths = {Path(p) for p in adapter.subagent_paths(pane, path)}
        except (OSError, ValueError, TypeError, sqlite3.Error):
            paths, unresolved = set(children), True
        children = self.child_tails[identity] = {p: t for p, t in children.items() if p in paths}
        for child_path in paths:
            child = children.setdefault(child_path, SessionTail(child_path, pane['agent']))
            try:
                child.read()
            except (OSError, ValueError, TypeError):
                unresolved = True
            unresolved = unresolved or child.last is None
        activity = [child.last for child in children.values() if child.last and child.last[0] in ('working', 'waiting')]
        pane['background_count'] = len(activity)
        self.active_children.pop(identity, None)
        if activity:
            self.active_children[identity] = len(activity)
        status, stamp = tail.last
        if status not in ('working', 'waiting'):
            if activity:
                status = 'working' if any(s == 'working' for s, _ in activity) else 'waiting'
                stamp = max(t for _, t in activity)
            elif unresolved:
                status = 'unknown'
            elif status == 'done':
                stamp = max([stamp] + [child.last[1] for child in children.values() if child.last])
                previous = self.signals.get(identity)
                if previous and previous[0] in ('working', 'waiting') and stamp < previous[1]:
                    # A closed/removed child has no final log event. Completion
                    # is observed now, not backdated to the parent's reply.
                    stamp = time.time()
        previous = self.signals.get(identity)
        if previous and previous[0] == status:
            stamp = max(stamp, previous[1])
        signal = status, stamp
        changed = signal != self.signals.get(identity)
        self.signals[identity] = signal
        if event or changed:
            if initial:
                # Rebuild observations without inventing a live completion
                # from a working baseline left by the previous dashboard.
                with db:
                    db.execute('''INSERT INTO observations VALUES(?,?,?,?)
                      ON CONFLICT(socket,pane) DO UPDATE SET owner=excluded.owner,status=excluded.status''',
                      (pane['socket'], pane['pane_id'], pane['pane_pid'], 'idle' if status == 'done' else status))
                if status == 'done':
                    current = db.execute('SELECT * FROM events WHERE socket=? AND pane=?',
                                         (pane['socket'], pane['pane_id'])).fetchone()
                    if current and current['owner'] == pane['pane_pid'] and current['agent'] == pane['agent'] and current['status'] == 'done':
                        return True  # Preserve both green completions and their acknowledgments.
                    # Recover a real completion after activity already tracked
                    # for this same session, including a previously missed
                    # child handback. A newly discovered idle agent stays ready.
                    tracked_activity = current and current['owner'] == pane['pane_pid'] and \
                        current['agent'] == pane['agent'] and current['session'] == str(path) and \
                        current['status'] in ('working', 'waiting') and stamp > current['updated']
                    if not tracked_activity:
                        status = 'idle'
            # Explicit idle/reset events also clear persisted activity on
            # reload, without turning a local command into a completion.
            with db:
                db.execute('''INSERT INTO events(socket,pane,status,updated,agent,session,owner)
                  VALUES(?,?,?,?,?,?,?) ON CONFLICT(socket,pane) DO UPDATE SET
                  status=excluded.status,updated=excluded.updated,agent=excluded.agent,
                  session=excluded.session,owner=excluded.owner''',
                  (pane['socket'], pane['pane_id'], status, stamp,
                   pane['agent'], str(path), pane['pane_pid']))
        return True

    def fallback(self, db, pane, status):
        identity = pane['identity']
        if status == 'idle':
            row = db.execute('SELECT status FROM observations WHERE socket=? AND pane=?',
                             (pane['socket'], pane['pane_id'])).fetchone()
            if row and row['status'] in ('working', 'waiting'):
                started = self.pending_idle.setdefault(identity, time.monotonic())
                if time.monotonic() - started < 1.5:
                    return
        else:
            self.pending_idle.pop(identity, None)
        observe(db, pane, status)


def scan_status(db, panes, tracker):
    live = {p['identity'] for p in panes}
    tracker.tails = {key: tail for key, tail in tracker.tails.items() if key in live}
    tracker.pending_idle = {key: value for key, value in tracker.pending_idle.items() if key in live}
    tracker.pending_wait = {key: value for key, value in tracker.pending_wait.items() if key in live}
    tracker.child_tails = {key: value for key, value in tracker.child_tails.items() if key in live}
    tracker.signals = {key: value for key, value in tracker.signals.items() if key in live}
    tracker.active_children = {key: count for key, count in tracker.active_children.items() if key in live}
    for pane in panes:
        pane['background_count'] = 0
        native, screen = False, None
        try:
            native = tracker.native(db, pane)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            pass
        try:
            if pane.get('kind', 'tmux') == 'tmux':
                result = tmux(pane['socket'], 'capture-pane', '-p', '-t', pane['pane_id'], check=False, timeout=1)
                text = result.stdout if result.returncode == 0 else None
            else:
                text = host_screen(pane)
            if text is not None:
                screen = display_status(pane['agent'], text, pane['pane_title'])
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        screen = tracker.stable_screen(pane, screen)
        if not native:
            tracker.fallback(db, pane, screen)
            if screen is None:
                row = db.execute('SELECT owner FROM events WHERE socket=? AND pane=?',
                                 (pane['socket'], pane['pane_id'])).fetchone()
                if not row or row['owner'] != pane['pane_pid']:
                    observe(db, pane, 'unknown')
        else:
            # Screen chrome supplies approval waits without inventing completion.
            signal = tracker.signals[pane['identity']]
            children_working = pane['identity'] in tracker.active_children
            if signal[0] == 'working' and (screen in ('working', 'waiting', 'interrupted') or
                                            (children_working and screen == 'idle')):
                screen_status = 'idle' if screen == 'interrupted' else screen
                if screen in ('idle', 'interrupted') and children_working:
                    screen_status = signal[0]
                with db:
                    db.execute('UPDATE events SET status=? WHERE socket=? AND pane=? AND owner=?',
                               (screen_status,
                                pane['socket'], pane['pane_id'], pane['pane_pid']))
        # Optional hooks report approval/input waits in hosts without a readable
        # terminal screen. Session IDs also work when there is no TMUX_PANE.
        adapter = registry().get(pane['agent'])
        try:
            sid = adapter.session_id(pane) if adapter else None
        except (OSError, ValueError, TypeError, KeyError):
            sid = None
        if sid:
            hook = db.execute('SELECT * FROM session_events WHERE agent=? AND session=?', (pane['agent'], sid)).fetchone()
            current = db.execute('SELECT * FROM events WHERE socket=? AND pane=?', (pane['socket'], pane['pane_id'])).fetchone()
            signal = tracker.signals.get(pane['identity'])
            newer_wait = hook and hook['status'] == 'waiting' and signal and \
                signal[0] == 'working' and hook['updated'] > signal[1] and \
                (not current or current['status'] in ('working', 'unknown'))
            if hook and (not current or hook['updated'] > current['updated'] or newer_wait):
                hook_status = hook['status']
                if signal and (pane['identity'] in tracker.active_children or signal[0] == 'unknown') and hook_status in ('done', 'idle'):
                    hook_status = signal[0]
                with db:
                    db.execute('''INSERT INTO events(socket,pane,status,updated,agent,session,owner)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(socket,pane) DO UPDATE SET
                      status=excluded.status,updated=excluded.updated,agent=excluded.agent,
                      session=excluded.session,owner=excluded.owner''',
                      (pane['socket'], pane['pane_id'], hook_status, hook['updated'],
                       pane['agent'], sid, pane['pane_pid']))
