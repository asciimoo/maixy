"""Discover live agents across tmux, terminals, and editor processes."""
import os
from pathlib import Path

from . import processes, hosts, tmux, editor
from .agents import registry


def external(record, agent, info, host):
    pid = record['pid']
    owner = str(pid) + ':' + record['started']
    cwd = info.get('cwd', '')
    return dict(kind='process', agent=agent, agent_pid=pid, socket='local',
                pane_id='pid:' + str(pid), pane_pid=owner, identity='local:' + owner,
                pane_tty=record['tty'], pane_current_path=cwd,
                pane_title=Path(cwd).name or agent.title(), window_name=host['host'],
                **host)


def discover(sock, session=None):
    records = processes.inventory()
    for record in records.values():
        record['agent'] = processes.agent_name(record) if record.get('uid', os.getuid()) == os.getuid() else None
    panes = tmux.discover(sock, session, processes.tree(records))
    # An explicit tmux session filter retains its original narrow scope.
    if session:
        return panes
    represented = {p['agent_pid'] for p in panes}
    adapters = registry()
    contexts = {}

    def context(agent):
        if agent not in contexts:
            contexts[agent] = adapters.get(agent).prepare()
        return contexts[agent]

    for pane in panes:
        adapters.get(pane['agent']).enrich(pane, context(pane['agent']))
    candidates = {pid: r['agent'] for pid, r in records.items()}
    candidates = {pid: agent for pid, agent in candidates.items() if agent and pid not in represented}
    details = processes.files(list(candidates))
    bridges = editor.bridges()
    host_names = {hosts.identify(pid, records)['host'] for pid in candidates if records[pid]['tty']}
    tabs = hosts.terminal_tabs(host_names)
    for pid, agent in candidates.items():
        record = records[pid]
        chain = list(processes.ancestors(pid, records))
        # Includes other tmux servers and descendants/internal worker processes.
        if any(Path(r['name']).name in ('tmux', 'tmux: server') or r.get('agent')
               for r in chain[1:]):
            continue
        host = hosts.identify(pid, records)
        pane = external(record, agent, details[pid], host)
        pane['pane_title'] = tabs.get(record['tty']) or pane['pane_title']
        editor.associate(pane, bridges)
        found = adapters.get(agent).discover(pane, record, details[pid], context(agent))
        panes.extend(found)
    return panes
