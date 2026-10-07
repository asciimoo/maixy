"""Dependency checks without changing the system Python installation."""
import subprocess
import sys

DEPENDENCIES = ['hidapi==0.15.0', 'Pillow==11.3.0']


def dependencies():
    try:
        import hid
        from PIL import Image
    except ImportError as error:
        raise RuntimeError('Missing dependencies. Run: maixy install-deps') from error


def install_dependencies():
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Use a virtual environment or scripts/install; system Python is not modified.')
    subprocess.run([sys.executable, '-m', 'pip', 'install', *DEPENDENCIES], check=True)

