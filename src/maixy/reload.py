"""Reload installed code via exec, retaining the dashboard's exclusive lock."""
import fcntl
import os
import sys

from .config import ROOT

LOCK_FD_ENV = '_MAIXY_RELOAD_LOCK_FD'


def acquire_lock():
    ROOT.mkdir(parents=True, exist_ok=True)
    filename = ROOT / 'run.lock'
    inherited = os.environ.pop(LOCK_FD_ENV, None)
    if inherited is not None:
        descriptor = int(inherited)
        actual, expected = os.fstat(descriptor), filename.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError('Reload lock does not match the state directory')
        lock = os.fdopen(descriptor, 'a')
        os.set_inheritable(descriptor, False)
    else:
        lock = filename.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('maixy is already running')
    return lock


def request_reload():
    filename = ROOT / 'run.lock'
    try:
        lock = filename.open('r')
    except FileNotFoundError:
        raise RuntimeError('No running maixy dashboard in ' + str(ROOT)) from None
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            (ROOT / 'reload.request').touch()
            print('maixy: reload requested', flush=True)
            return
    raise RuntimeError('No running maixy dashboard in ' + str(ROOT))


def consume_request():
    try:
        (ROOT / 'reload.request').unlink()
    except FileNotFoundError:
        return False
    return True


def restart(lock):
    descriptor = lock.fileno()
    os.set_inheritable(descriptor, True)
    os.environ[LOCK_FD_ENV] = str(descriptor)
    try:
        os.execv(sys.executable, [sys.executable, '-m', 'maixy', *sys.argv[1:]])
    finally:
        # Only reached if exec fails (or is mocked in a test).
        os.environ.pop(LOCK_FD_ENV, None)
        os.set_inheritable(descriptor, False)
