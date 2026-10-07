"""Shared POSIX process inventory (procps on Linux)."""
from ..tmux import run


def inventory():
    # Both macOS and Linux ps expose these fields. lstart identifies PID reuse.
    output = run(['ps', '-axo', 'pid=,ppid=,uid=,tty=,lstart=,comm=']).stdout
    records = {}
    for line in output.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10:
            continue
        pid, parent, uid = map(int, fields[:3])
        tty = fields[3]
        # Keep root-owned login intermediaries for host ancestry. Discovery
        # restricts agent candidates to this user's UID.
        records[pid] = dict(pid=pid, parent=parent, uid=uid, name=fields[9],
                            tty='' if tty in ('?', '??') else '/dev/' + tty,
                            started=' '.join(fields[4:9]))
    return records

