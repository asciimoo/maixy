"""x11 graphical navigation backend."""
from ..tmux import run
from .windows import unique_window


class Navigator:
    def focus(self, pane, foreground=True):
        if not foreground:
            raise RuntimeError("Standalone window navigation requires application focus")
        pids = set(pane.get("host_chain", []))
        matches = []
        for line in run(['wmctrl', '-lp']).stdout.splitlines():
            fields = line.split(None, 4)
            if len(fields) == 5 and fields[2].isdigit() and int(fields[2]) in pids:
                matches.append(dict(id=fields[0], name=fields[4]))
        window = unique_window(matches, pane)
        run(['wmctrl', '-ia', window['id']])
        return
