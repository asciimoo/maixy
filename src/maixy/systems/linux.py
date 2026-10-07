"""Linux process file and argument inspection through /proc."""
from pathlib import Path

from .posix import inventory

PROC = Path('/proc')


def argv(pid):
    try:
        return [part.decode(errors='replace') for part in
                (PROC / str(pid) / 'cmdline').read_bytes().split(b'\0') if part]
    except OSError:
        return []


def files(pids):
    found = {pid: dict(cwd='', logs=[]) for pid in pids}
    for pid in pids:
        root = PROC / str(pid)
        try:
            found[pid]['cwd'] = str((root / 'cwd').resolve(strict=True))
            for fd in (root / 'fd').iterdir():
                try:
                    path = str(fd.resolve(strict=True))
                    if path.endswith('.jsonl'):
                        found[pid]['logs'].append(path)
                except OSError:
                    pass
        except OSError:
            pass
    return found


def terminal_tabs(host_names=()):
    return {}


def screen(pane):
    # There is no desktop-wide API for arbitrary terminal screen contents.
    # Native session logs/hooks provide status; terminal-specific adapters can
    # add screen access without changing discovery or the dashboard.
    return None
