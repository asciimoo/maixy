import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maixy.config import state_home


class ConfigTests(unittest.TestCase):
    def test_maixy_home_takes_precedence(self):
        with patch.dict(os.environ, {'MAIXY_HOME': '/tmp/maixy-test', 'LOGIAI_HOME': '/tmp/logiai-test'}):
            self.assertEqual(state_home(), Path('/tmp/maixy-test'))

    def test_legacy_state_is_reused_and_new_install_uses_maixy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('maixy.config.Path.home', return_value=root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(state_home(), root / '.local/share/maixy')
                legacy = root / '.local/share/logiai'
                legacy.mkdir(parents=True)
                (legacy / 'state.sqlite3').touch()
                self.assertEqual(state_home(), legacy)

