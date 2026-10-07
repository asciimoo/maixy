"""Discovery/status remain available without a graphical navigation integration."""


class Navigator:
    def focus(self, pane, foreground=True):
        raise RuntimeError('No graphical navigator selected. Use --no-focus for tmux, '
                           '--navigator for your desktop, or MAIXY_NAVIGATION_COMMAND.')
