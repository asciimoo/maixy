import json
import os
import subprocess


class Navigator:
    def focus(self, pane, foreground=True):
        try:
            command = json.loads(os.environ['MAIXY_NAVIGATION_COMMAND'])
        except (ValueError, KeyError) as error:
            raise RuntimeError('MAIXY_NAVIGATION_COMMAND must be a JSON array of argv strings') from error
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
            raise RuntimeError('MAIXY_NAVIGATION_COMMAND must be a nonempty JSON array of argv strings')
        # No shell interpolation. Tokens and conversation text are excluded.
        data = {k: v for k, v in pane.items() if k != 'editor_bridge'}
        data['foreground'] = foreground
        result = subprocess.run(command, input=json.dumps(data), text=True, capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Custom navigation failed')
