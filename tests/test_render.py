import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from maixy.config import DONE, NORMAL, WAITING, WORKING
from maixy.render import render


class RenderTests(unittest.TestCase):
    def test_status_backgrounds(self):
        pane = dict(agent='codex', window_name='work', pane_title='A long agent title with spaces')
        for status, color in [('working', WORKING), ('waiting', WAITING), ('done', DONE), ('idle', NORMAL)]:
            with self.subTest(status=status):
                image = Image.open(io.BytesIO(render(pane, status)))
                self.assertEqual(image.size, (118, 118))
                expected = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
                actual = image.getpixel((117, 117))
                self.assertLess(max(abs(a - b) for a, b in zip(expected, actual)), 8)

    def test_render_uses_bundled_font_when_no_system_font_is_available(self):
        pane = dict(agent='claude', window_name='Terminal', pane_title='Standalone agent', background_count=12)
        with patch('pathlib.Path.exists', return_value=False):
            image = Image.open(io.BytesIO(render(pane, 'waiting')))
        self.assertEqual(image.size, (118, 118))

    def test_background_count_is_visible_only_in_header(self):
        for agent in ('codex', 'claude', 'custom-agent-with-a-long-name'):
            for status in ('working', 'waiting', 'done', 'idle', 'unknown'):
                with self.subTest(agent=agent, status=status):
                    pane = dict(agent=agent, window_name='work', pane_title='Agent title')
                    with Image.open(io.BytesIO(render(pane, status))) as empty:
                        self.assertEqual(render(pane, status), render(dict(pane, background_count=0), status))
                        for count in (1, 12, 9999):
                            with Image.open(io.BytesIO(render(dict(pane, background_count=count), status))) as counted:
                                self.assertEqual(counted.size, (118, 118))
                                self.assertIsNotNone(ImageChops.difference(empty.crop((7, 0, 112, 20)),
                                                                          counted.crop((7, 0, 112, 20))).getbbox())
                                self.assertEqual(empty.crop((0, 24, 118, 118)).tobytes(),
                                                 counted.crop((0, 24, 118, 118)).tobytes())
                                # No header glyph may cross the key's margins.
                                self.assertEqual(empty.crop((112, 0, 118, 16)).tobytes(),
                                                 counted.crop((112, 0, 118, 16)).tobytes())
