"""Optional macOS user LaunchAgent; no system service or elevated user."""
import fcntl
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from .config import ROOT
from .tmux import socket_path

LABEL = 'local.maixy.dashboard'
LAUNCHCTL = '/bin/launchctl'


def plist_path():
    return Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def launchctl(*args, check=True):
    result = subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError('launchctl ' + args[0] + ' failed: ' + (result.stderr.strip() or str(result.returncode)))
    return result


def specification(args):
    root = ROOT.expanduser().absolute()
    command = [sys.executable, '-m', 'maixy', '--socket', str(Path(socket_path(args.socket)).absolute()),
               '--interval', str(args.interval)]
    for option in ('session', 'client', 'navigator'):
        value = getattr(args, option, None)
        if value:
            command.extend(['--' + option, value])
    if args.no_focus:
        command.append('--no-focus')
    if getattr(args, 'toggle_focus', False):
        command.append('--toggle-focus')
    environment = {'MAIXY_HOME': str(root),
                   'PATH': os.environ.get('PATH', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin')}
    # Preserve only supported configuration, never the calling agent's whole
    # environment, bridge credentials, or an inherited reload lock descriptor.
    for key in ('MAIXY_NAVIGATOR', 'MAIXY_NAVIGATION_COMMAND', 'MAIXY_AGENTS_FILE', 'PYTHONPATH'):
        if os.environ.get(key):
            environment[key] = os.environ[key]
    return dict(Label=LABEL, ProgramArguments=command, EnvironmentVariables=environment,
                RunAtLoad=True, KeepAlive=True, ThrottleInterval=10,
                WorkingDirectory=str(Path.cwd()),
                StandardOutPath=str(root / 'dashboard.log'),
                StandardErrorPath=str(root / 'dashboard.log'))


def dashboard_running():
    try:
        lock = (ROOT / 'run.lock').open('r')
    except FileNotFoundError:
        return False
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def install(args, remove=False):
    if sys.platform != 'darwin':
        raise RuntimeError('Automatic startup currently supports macOS LaunchAgents only')
    target = 'gui/' + str(os.getuid()) + '/' + LABEL
    loaded = launchctl('print', target, check=False).returncode == 0
    path = plist_path()
    if remove:
        if loaded:
            launchctl('bootout', target)
        if path.exists():
            path.unlink()
        print('Removed Maixy automatic startup; state and logs retained.')
        return
    if args.interval <= 0:
        raise RuntimeError('--interval must be positive')
    if dashboard_running() and not loaded:
        raise RuntimeError('Stop the manually started Maixy dashboard before installing automatic startup; only one instance can use the keypad')
    spec = specification(args)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Prepare the complete plist before changing a running service.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.plist.tmp')
    with temporary.open('wb') as stream:
        plistlib.dump(spec, stream)
    temporary.chmod(0o600)
    if loaded:
        launchctl('bootout', target)
        deadline = time.monotonic() + 5
        while dashboard_running():
            if time.monotonic() >= deadline:
                temporary.unlink()
                raise RuntimeError('Previous dashboard is still stopping; retry install-autostart shortly')
            time.sleep(.05)
    temporary.replace(path)
    launchctl('enable', target)
    launchctl('bootstrap', 'gui/' + str(os.getuid()), str(path))
    print('Installed Maixy automatic startup: ' + str(path))
    print('Dashboard log: ' + str(ROOT / 'dashboard.log'))
