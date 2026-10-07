"""OS process inventory. No shell commands or process environments are read."""
import collections

from .systems import current as system
from .agents import process_agent


def inventory():
    return system().inventory()


def tree(records):
    children, names = collections.defaultdict(list), {}
    for pid, record in records.items():
        children[record['parent']].append(pid)
        names[pid] = record.get('agent') or record['name']
    return children, names


def ancestors(pid, records):
    seen = set()
    while pid in records and pid not in seen:
        seen.add(pid)
        yield records[pid]
        pid = records[pid]['parent']


def agent_name(record):
    return process_agent(record, system().argv)


def files(pids):
    """Return cwd and open session logs through the OS adapter."""
    return system().files(pids)
