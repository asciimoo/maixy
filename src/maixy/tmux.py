import collections
import os
from pathlib import Path
import shlex
import subprocess
import time

from .config import TMUX, SEP


def run(args, check=True, timeout=5):
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip() or 'Command failed: ' + shlex.join(args))
    return p


def socket_path(explicit=None):
    if explicit:
        return explicit
    if os.environ.get('TMUX'):
        return os.environ['TMUX'].rsplit(',', 2)[0]
    return str(Path(os.environ.get('TMUX_TMPDIR', '/tmp')) / ('tmux-' + str(os.getuid())) / 'default')


def tmux(sock, *args, **kw):
    return run([TMUX, '-S', sock, *args], **kw)


def processes():
    from . import processes as local
    records = local.inventory()
    for record in records.values():
        record['agent'] = local.agent_name(record) if record.get('uid', os.getuid()) == os.getuid() else None
    return local.tree(records)


def agent_for(pid, children, names):
    return agent_process(pid, children, names)[0]


def agent_process(pid, children, names):
    # Inspect live descendants rather than trusting pane_current_command.
    from .agents import registry
    adapters = registry()
    todo = collections.deque([pid])
    seen = set()
    while todo:
        p = todo.popleft()
        if p in seen:
            continue
        seen.add(p)
        name = Path(names.get(p, '')).name.lower()
        adapter = adapters.get(name)
        agent = adapter.name if adapter else adapters.match(name)
        if agent:
            return agent, p
        todo.extend(children[p])
    return None, None


def discover(sock, session=None, process_tree=None):
    fields = ['session_id', 'session_name', 'window_id', 'window_name', 'pane_id',
              'pane_title', 'pane_pid', 'pane_current_command', 'pane_tty', 'pane_dead', 'pane_current_path']
    fmt = SEP.join('#{' + f + '}' for f in fields)
    try:
        p = tmux(sock, 'list-panes', '-a', '-F', fmt, check=False)
    except FileNotFoundError:
        return []
    if p.returncode:
        if 'no server running' in p.stderr or 'No such file' in p.stderr or 'failed to connect to server' in p.stderr:
            return []
        raise RuntimeError(p.stderr.strip())
    children, names = process_tree if process_tree is not None else processes()
    result = []
    for line in p.stdout.splitlines():
        bits = line.split(SEP)
        if len(bits) != len(fields):
            continue
        pane = dict(zip(fields, bits))
        if pane['pane_dead'] == '1' or (session and session not in (pane['session_name'], pane['session_id'])):
            continue
        agent, agent_pid = agent_process(int(pane['pane_pid']), children, names)
        if not agent:
            continue
        pane.update(agent=agent, agent_pid=agent_pid, socket=sock, kind='tmux',
                    identity=sock + ':' + pane['pane_id'] + ':' + pane['pane_pid'])
        result.append(pane)
    return result


def jump(db, pane, client=None, foreground=True):
    sock = pane['socket']
    clients_fmt = SEP.join(['#{client_name}', '#{client_session}', '#{client_activity}', '#{client_tty}'])
    clients = []
    for line in tmux(sock, 'list-clients', '-F', clients_fmt).stdout.splitlines():
        bits = line.split(SEP)
        if len(bits) == 4:
            clients.append(bits)
    if client:
        selected = next((c for c in clients if client in (c[0], c[3])), None)
    else:
        selected = max(clients, key=lambda c: int(c[2] or 0), default=None)
    if not selected:
        raise RuntimeError('No attached tmux client' + (': ' + client if client else ''))
    # Stable tmux IDs avoid selecting the wrong pane after renames/reordering.
    tmux(sock, 'switch-client', '-c', selected[0], '-t', pane['session_id'])
    tmux(sock, 'select-window', '-t', pane['window_id'])
    tmux(sock, 'select-pane', '-t', pane['pane_id'])
    with db:
        db.execute('UPDATE events SET acknowledged=? WHERE socket=? AND pane=?',
                   (time.time(), sock, pane['pane_id']))
