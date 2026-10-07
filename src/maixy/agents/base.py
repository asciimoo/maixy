"""Agent adapter contract, independent of operating systems and navigation."""
import fnmatch
from pathlib import Path
import re


class Agent:
    """Subclass for native session support; simple CLIs need only matchers.

    Discovery methods receive live process metadata, never saved history.
    Status methods return working/waiting/idle/done/interrupted or None.
    """

    hook_events = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
                   'PermissionRequest', 'Stop', 'SessionEnd')
    hook_notification_matcher = None

    def __init__(self, name, executables=(), scripts=(), modules=(), display=None):
        self.name = name
        self.executables = tuple(executables)
        self.scripts = tuple(scripts)
        self.modules = tuple(modules)
        self.display = {status: tuple(re.compile(pattern, re.I) for pattern in patterns)
                        for status, patterns in (display or {}).items()}

    def matches(self, executable, script='', module=''):
        return (executable in self.executables or
                bool(script and (Path(script).name.lower() in self.executables or
                                 any(fnmatch.fnmatchcase(script, p) for p in self.scripts))) or
                bool(module and module in self.modules))

    def prepare(self):
        """Create per-discovery context, shared by tmux and standalone matches."""
        return {}

    def enrich(self, pane, context):
        """Attach native session metadata to an already discovered tmux pane."""

    def discover(self, pane, record, files, context):
        # A bare server/worker process is not an interactive agent session.
        return [pane] if record['tty'] or pane.get('terminal_pid') else []

    def resolve(self, pane):
        path = pane.get('session_path')
        return Path(path) if path else None

    def session_id(self, pane):
        return pane.get('session_id') if pane.get('kind') == 'process' else None

    def subagent_paths(self, pane, path):
        """Return child lifecycle logs belonging to this live parent session."""
        return []

    def display_status(self, text, title=''):
        lines = [line.strip() for line in text.splitlines()[-18:]]
        for status in ('working', 'waiting', 'interrupted', 'idle'):
            if any(pattern.search(line) for pattern in self.display.get(status, ()) for line in lines):
                return status
        return None

    def event_status(self, obj):
        return None

    def hook_path(self):
        return None

    def hook_notice(self):
        return None

    def hook_status(self, event, payload):
        if payload.get('agent_id') or payload.get('agent_type') or payload.get('parent_session_id'):
            return None
        if event == 'Notification' and payload.get('notification_type') == 'idle_prompt':
            return None
        return {'SessionStart': 'idle', 'UserPromptSubmit': 'working',
                'PreToolUse': 'working', 'PostToolUse': 'working',
                'PermissionRequest': 'waiting', 'Stop': 'done',
                'Interrupt': 'idle', 'SessionEnd': 'idle', 'Notification': 'waiting'}.get(event)
