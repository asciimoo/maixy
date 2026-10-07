"""Runtime locations and keypad colors."""
import os
from pathlib import Path
import shutil


def state_home():
    override = os.environ.get('MAIXY_HOME') or os.environ.get('LOGIAI_HOME')
    if override:
        return Path(override).expanduser()
    legacy = Path.home() / '.local/share/logiai'
    if (legacy / 'state.sqlite3').exists():
        return legacy
    return Path.home() / '.local/share/maixy'


ROOT = state_home()
TMUX = shutil.which('tmux') or 'tmux'
SEP = '\x1f'
NORMAL, WORKING, WAITING, DONE = '#000000', '#79b8ff', '#ffbc66', '#7ee2a8'
