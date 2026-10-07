import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import time

from .agents import registry
from .config import ROOT
from .state import connect
from .tmux import socket_path, tmux, processes, agent_for

HOOK_COMMAND = shlex.join([sys.executable, "-m", "maixy"])


def is_maixy_hook(command):
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if len(args) >= 2 and Path(args[0]).name in ('maixy', 'logiai') and args[1] == 'hook':
        return True
    return len(args) >= 4 and args[1:4] == ['-m', 'maixy', 'hook']


def hook(agent, event):
    # Hooks must never block an agent or emit terminal noise. JSON is valid for
    # Codex's Stop hook and harmless for other lifecycle events.
    try:
        payload = json.load(sys.stdin)
        adapter = registry().get(agent)
        state = adapter.hook_status(event, payload) if adapter else None
        if not state:
            return
        now = time.time()
        sid = payload.get('session_id', '')
        if sid:
            with contextlib.closing(connect()) as db, db:
                db.execute('''INSERT INTO session_events VALUES(?,?,?,?)
                  ON CONFLICT(agent,session) DO UPDATE SET status=excluded.status,updated=excluded.updated''',
                  (agent, sid, state, now))
        pane = os.environ.get('TMUX_PANE', '')
        if not re.fullmatch(r'%\d+', pane) or not os.environ.get('TMUX'):
            return
        sock = socket_path()
        owner = tmux(sock, 'display-message', '-p', '-t', pane, '#{pane_pid}', timeout=1).stdout.strip()
        # Daemon-launched Codex hooks may inherit TMUX_PANE from a different
        # terminal. Never write a status to a pane belonging to another agent.
        children, names = processes()
        if agent_for(int(owner), children, names) != agent:
            return
        with contextlib.closing(connect()) as db, db:
            db.execute('''INSERT INTO events(socket,pane,status,updated,agent,session,owner)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(socket,pane) DO UPDATE SET
              status=excluded.status,updated=excluded.updated,agent=excluded.agent,
              session=excluded.session,owner=excluded.owner''',
              (sock, pane, state, now, agent, payload.get('session_id', ''), owner))
    except Exception as e:
        # Log only errors, never prompts or agent output.
        try:
            ROOT.mkdir(parents=True, exist_ok=True)
            with (ROOT / 'hook-errors.log').open('a') as f:
                f.write(str(e) + '\n')
        except OSError:
            pass
    finally:
        print('{}')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.logiai-tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    else:
        os.chmod(tmp, 0o600)
    tmp.replace(path)


def install_hooks(remove=False):
    for adapter in registry().adapters.values():
        agent, path = adapter.name, adapter.hook_path()
        if path is None:
            continue
        obj = json.loads(path.read_text()) if path.exists() else {}
        before = json.dumps(obj, sort_keys=True)
        hooks = obj.setdefault('hooks', {})
        for event, groups in list(hooks.items()):
            kept = []
            for group in groups:
                g = dict(group)
                g['hooks'] = [h for h in group.get('hooks', [])
                              if not is_maixy_hook(h.get('command', ''))]
                if g['hooks']:
                    kept.append(g)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if not remove:
            for event in adapter.hook_events:
                handler = {'type': 'command',
                           'command': HOOK_COMMAND + ' hook ' + agent + ' ' + event,
                           'timeout': 3}
                group = {'hooks': [handler]}
                if event == 'Notification' and adapter.hook_notification_matcher:
                    group['matcher'] = adapter.hook_notification_matcher
                hooks.setdefault(event, []).append(group)
        if before != json.dumps(obj, sort_keys=True):
            if path.exists():
                backup = ROOT / (agent + '-hooks-backup-' + str(time.time_ns()) + '.json')
                shutil.copy2(path, backup)
            atomic_json(path, obj)
        print(('Removed from ' if remove else 'Installed in ') + str(path))
        if not remove and adapter.hook_notice():
            print(adapter.hook_notice())
