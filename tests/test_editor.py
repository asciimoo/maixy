import http.server
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from maixy import editor


class BridgeTests(unittest.TestCase):
    def test_authenticated_bridge_discovery_selection_and_stale_terminal(self):
        selected = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.assert_auth()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps([dict(pid=10, name='Backend', workspace='Project')]).encode())

            def assert_auth(self):
                if self.headers.get('Authorization') != 'Bearer test-token':
                    raise AssertionError('Missing bridge authentication')

            def do_POST(self):
                self.assert_auth()
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                selected.append(data['pid'])
                self.send_response(200 if data['pid'] == 10 else 404)
                self.end_headers()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge_dir = root / 'editor-bridges'
            bridge_dir.mkdir()
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                (bridge_dir / 'bridge.json').write_text(json.dumps(dict(port=server.server_port, token='test-token')))
                (bridge_dir / 'stale.json').write_text('{}')
                with patch.object(editor, 'ROOT', root):
                    bridges = editor.bridges()
                self.assertEqual(len(bridges), 1)
                pane = dict(host_chain=[11, 10, 1], pane_title='Fallback')
                editor.associate(pane, bridges)
                self.assertEqual(pane['terminal_pid'], 10)
                self.assertEqual(pane['pane_title'], 'Backend')
                editor.select(pane)
                self.assertEqual(selected, [10])
                pane['terminal_pid'] = 20
                with self.assertRaises(RuntimeError):
                    editor.select(pane)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
