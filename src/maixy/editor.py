"""Optional local VS Code terminal bridge; independent of OS navigation."""
import http.client
import json
from pathlib import Path

from .config import ROOT


def bridges():
    result = []
    for path in (ROOT / 'editor-bridges').glob('*.json'):
        try:
            meta = json.loads(path.read_text())
            connection = http.client.HTTPConnection('127.0.0.1', int(meta['port']), timeout=.5)
            try:
                connection.request('GET', '/terminals', headers={'Authorization': 'Bearer ' + meta['token']})
                response = connection.getresponse()
                if response.status == 200:
                    result.append(dict(meta, terminals=json.loads(response.read(1024 * 1024))))
            finally:
                connection.close()
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            continue
    return result


def associate(pane, available):
    for bridge in available:
        for terminal in bridge['terminals']:
            if terminal.get('pid') in pane.get('host_chain', []):
                pane.update(editor_bridge=bridge, terminal_pid=terminal['pid'],
                            window_name=bridge.get('application', 'VS Code'),
                            pane_title=terminal.get('name') or pane['pane_title'],
                            window_title=terminal.get('workspace', ''))
                return


def select(pane):
    bridge = pane['editor_bridge']
    connection = http.client.HTTPConnection('127.0.0.1', int(bridge['port']), timeout=2)
    try:
        connection.request('POST', '/focus', json.dumps({'pid': pane['terminal_pid']}),
                           {'Authorization': 'Bearer ' + bridge['token'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('Editor terminal is no longer available; completion remains unacknowledged')
        response.read()
    finally:
        connection.close()
