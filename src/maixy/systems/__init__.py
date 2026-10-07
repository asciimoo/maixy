"""OS adapters expose inventory(), argv(pid), files(pids), terminal_tabs(), screen()."""
import sys


def current():
    if sys.platform == 'darwin':
        from . import macos
        return macos
    if sys.platform.startswith('linux'):
        from . import linux
        return linux
    raise RuntimeError('Unsupported process platform: ' + sys.platform)
