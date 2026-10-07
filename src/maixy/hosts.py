"""Host identification and OS terminal access dispatch."""

from .processes import ancestors
from .systems import current as system

HOSTS = (
    ('/Terminal.app/', 'Terminal'), ('/iTerm.app/', 'iTerm'),
    ('/Visual Studio Code.app/', 'VS Code'), ('/Code - Insiders.app/', 'VS Code Insiders'),
    ('/Cursor.app/', 'Cursor'), ('/Ghostty.app/', 'Ghostty'),
    ('code-insiders', 'VS Code Insiders'), ('code', 'VS Code'), ('cursor', 'Cursor'),
    ('gnome-terminal', 'GNOME Terminal'), ('konsole', 'Konsole'),
    ('kitty', 'kitty'), ('alacritty', 'Alacritty'), ('wezterm', 'WezTerm'),
    ('ghostty', 'Ghostty'), ('foot', 'foot'), ('xterm', 'xterm'),
)


def identify(pid, records):
    chain = list(ancestors(pid, records))
    for record in reversed(chain):
        for fragment, label in HOSTS:
            name = record['name']
            executable = name.rsplit('/', 1)[-1].lower()
            if ('/' in fragment and fragment in name) or (executable == fragment or executable.startswith(fragment + ' ') or executable.startswith(fragment + '-')):
                return dict(host=label, host_pid=record['pid'], host_chain=[r['pid'] for r in chain])
    return dict(host='Terminal process' if records.get(pid, {}).get('tty') else 'Background process',
                host_pid=None, host_chain=[r['pid'] for r in chain])


def terminal_tabs(host_names=('Terminal',)):
    return system().terminal_tabs(host_names)


def screen(pane):
    return system().screen(pane)
