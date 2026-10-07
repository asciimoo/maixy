"""Claude Code process/session metadata and lifecycle events."""
import json
from pathlib import Path
import re

from .base import Agent


def metadata(pid):
    try:
        return json.loads((Path.home() / '.claude/sessions' / (str(pid) + '.json')).read_text())
    except (OSError, ValueError):
        return {}


TASK_ENDED = frozenset(('completed', 'failed', 'stopped', 'killed'))


def task_notifications(obj):
    """Extract Claude's task lifecycle envelope, not mentions in ordinary prose."""
    if obj.get('type') == 'system' and obj.get('subtype') == 'task_notification':
        return [(obj.get('task_id'), obj.get('status'))]
    kind = obj.get('type')
    if kind == 'user':
        content = obj.get('message', {}).get('content', '')
    elif kind == 'queue-operation' and obj.get('operation') == 'enqueue':
        # Background completions can enter the input queue while Claude is
        # busy. They are lifecycle notifications before delivery to a turn.
        content = obj.get('content', '')
    elif kind == 'attachment':
        attachment = obj.get('attachment', {})
        if not isinstance(attachment, dict) or attachment.get('type') != 'queued_command' or \
                attachment.get('humanTurn') is True:
            return []
        origin = attachment.get('origin', {})
        task_origin = isinstance(origin, dict) and origin.get('kind') == 'task-notification'
        if not task_origin and attachment.get('commandMode') != 'task-notification':
            return []
        content = attachment.get('prompt', '')
    else:
        return []
    texts = [content] if isinstance(content, str) else []
    if isinstance(content, list):
        texts = [part.get('text', '') for part in content
                 if isinstance(part, dict) and part.get('type') == 'text']
    events = []
    for text in texts:
        if not isinstance(text, str):
            continue
        envelope = re.match(r'\s*<task-notification>(.*?)</task-notification>', text, re.S)
        if envelope:
            task = re.search(r'<task-id>([\w-]+)</task-id>', envelope[1])
            status = re.search(r'<status>([\w-]+)</status>', envelope[1])
            if task and status:
                events.append((task[1], status[1]))
    return events


class BackgroundTasks:
    """Shell jobs may outlive both the parent's reply and a subagent's handback."""

    def __init__(self):
        self.active = {}
        self.updated = 0
        self.stops = {}

    def finish(self, task, stamp):
        if isinstance(task, str) and task in self.active:
            del self.active[task]
            self.updated = max(self.updated, stamp)

    def observe(self, obj, stamp):
        for task, status in task_notifications(obj):
            if status in TASK_ENDED:
                self.finish(task, stamp)
        content = obj.get('message', {}).get('content', [])
        if not isinstance(content, list):
            return
        for part in content:
            if not isinstance(part, dict):
                continue
            tool_id = part.get('id')
            inputs = part.get('input', {})
            if obj.get('type') == 'assistant' and part.get('type') == 'tool_use' and \
                    part.get('name') == 'TaskStop' and isinstance(tool_id, str) and isinstance(inputs, dict):
                task = inputs.get('task_id')
                if isinstance(task, str):
                    self.stops[tool_id] = task
            if obj.get('type') != 'user' or part.get('type') != 'tool_result':
                continue
            tool_id = part.get('tool_use_id')
            stopped = self.stops.pop(tool_id, None) if isinstance(tool_id, str) else None
            if part.get('is_error'):
                continue
            if stopped:
                self.finish(stopped, stamp)
            result = obj.get('toolUseResult')
            if not isinstance(result, dict):
                continue
            task = result.get('backgroundTaskId')
            if isinstance(task, str) and task:
                self.active[task] = stamp
            # TaskOutput can observe completion before its notification arrives.
            task = result.get('task')
            if isinstance(task, dict) and task.get('status') in TASK_ENDED:
                self.finish(task.get('task_id'), stamp)


class Claude(Agent):
    hook_events = Agent.hook_events + ('Notification',)
    hook_notification_matcher = 'permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input'

    def __init__(self):
        super().__init__('claude', ('claude', 'claude-code'), ('*/@anthropic-ai/claude-code/*',))

    def discover(self, pane, record, files, context):
        meta = metadata(record['pid'])
        if meta.get('agentId') or meta.get('parentSessionId'):
            return []
        if not record['tty'] and meta.get('pid') != record['pid']:
            return []
        pane['pane_title'] = meta.get('name') or pane['pane_title']
        pane['pane_current_path'] = meta.get('cwd') or pane['pane_current_path']
        return [pane]

    def resolve(self, pane):
        path = super().resolve(pane)
        if path:
            return path
        sid = self.session_id(pane)
        if not sid or not re.fullmatch(r'[\w-]+', sid):
            return None
        paths = list((Path.home() / '.claude/projects').glob('*/' + sid + '.jsonl'))
        return paths[0] if len(paths) == 1 else None

    def session_id(self, pane):
        meta = metadata(pane['agent_pid'])
        return meta.get('sessionId') if meta.get('pid') == pane['agent_pid'] else None

    def subagent_paths(self, pane, path):
        return list((path.parent / path.stem / 'subagents').glob('agent-*.jsonl'))

    def background_tracker(self):
        return BackgroundTasks()

    def display_status(self, text, title=''):
        lines = [line.strip() for line in text.splitlines()[-18:]]
        footer = '\n'.join(lines)
        # Question dialogs can retain an activity spinner above their choices.
        # Require both selection UI and its keyboard hints, not question prose.
        if any(re.match(r'^❯\s*(?:\d+[.)]|[☐☑])', line) for line in lines) and \
                re.search(r'(?i)(?:enter to (?:select|submit|confirm)|space to (?:select|toggle))', footer) and \
                re.search(r'(?i)(?:esc to cancel|(?:↑|↓|arrow).*navigate)', footer):
            return 'waiting'
        spinner = r'^[✻✽✶✢✳·⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+\S.*(?:…|\.\.\.)'
        if any(re.search(spinner, line) and
               (re.search(r'\d+(?:m|s)\b|tokens|interrupt', line, re.I) or
                re.search(r'(?:esc|ctrl.c) to interrupt', footer, re.I)) for line in lines):
            return 'working'
        if any(re.match(r'^(?:Do you want to (?:proceed|allow)|Allow .*\?|'
                             r'Claude needs your (?:permission|approval)|Permission required)',
                        line, re.I) for line in lines):
            return 'waiting'
        if any(line.startswith('❯') for line in lines):
            if any('Interrupted ·' in line for line in lines):
                return 'interrupted'
            return 'idle'
        return None

    def event_status(self, obj):
        # Task notifications are lifecycle metadata, not new user prompts.
        # Their completion updates the per-log task tracker separately.
        if task_notifications(obj):
            return None
        kind, message = obj.get('type'), obj.get('message', {})
        content = message.get('content', '')
        if kind == 'assistant' and isinstance(content, list) and any(
                isinstance(part, dict) and part.get('type') == 'tool_use' and
                part.get('name') == 'AskUserQuestion' for part in content):
            return 'waiting'
        if kind == 'assistant':
            if message.get('stop_reason') == 'end_turn':
                return 'done'
            # Background results can resume an assistant without a new user
            # prompt. Active assistant blocks supersede its previous end_turn.
            if message.get('stop_reason') == 'tool_use' or isinstance(content, list) and any(
                    isinstance(part, dict) and part.get('type') in ('thinking', 'text', 'tool_use')
                    for part in content):
                return 'working'
        if kind == 'user' and not obj.get('isMeta'):
            # Some subagents finish through SubagentHandback instead of an
            # assistant end_turn or turn_duration record. The result carries
            # an explicit lifecycle flag; ordinary tool results stay active.
            if obj.get('toolEndsTurn') is True and isinstance(content, list) and any(
                    isinstance(part, dict) and part.get('type') == 'tool_result' and
                    not part.get('is_error') for part in content):
                return 'done'
            texts = [content] if isinstance(content, str) else []
            if isinstance(content, list):
                texts = [p.get('text', '') for p in content
                         if isinstance(p, dict) and p.get('type') == 'text']
            # Local slash commands are stored as user messages, but do not
            # start an assistant turn or produce an end_turn response.
            if any(isinstance(text, str) and text.lstrip().startswith('<local-command-stdout>') for text in texts):
                return 'idle'
            if any(isinstance(text, str) and text.lstrip().startswith('<command-name>') for text in texts):
                return None
            if isinstance(content, str) or (isinstance(content, list) and
                any(isinstance(p, dict) and p.get('type') in ('text', 'tool_result') for p in content)):
                return 'working'
        if kind == 'system' and obj.get('subtype') == 'turn_duration':
            return 'done'
        if kind == 'system' and obj.get('subtype') == 'local_command':
            return 'idle'
        return None

    def hook_path(self):
        return Path.home() / '.claude/settings.json'

    def hook_status(self, event, payload):
        status = super().hook_status(event, payload)
        if status == 'done' and event == 'Stop' and any(
                isinstance(task, dict) and task.get('status') not in TASK_ENDED
                for task in payload.get('background_tasks', []) or []):
            return 'working'
        if status == 'working' and event == 'PreToolUse' and payload.get('tool_name') == 'AskUserQuestion':
            return 'waiting'
        return status

    def hook_notice(self):
        return 'Existing Claude sessions: restart/resume to load hooks.'
