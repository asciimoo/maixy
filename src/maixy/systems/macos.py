"""macOS process files, argv, and scriptable terminal access."""
import shlex
from ..tmux import run, SEP
from .posix import inventory


def argv(pid):
    output = run(['ps', '-p', str(pid), '-o', 'args='], check=False).stdout
    try:
        return shlex.split(output)
    except ValueError:
        return []


def files(pids):
    found = {pid: dict(cwd='', logs=[]) for pid in pids}
    if not pids:
        return found
    output = run(['/usr/sbin/lsof', '-n', '-P', '-a', '-p', ','.join(map(str, pids)),
                  '-F', 'pfn'], check=False, timeout=5).stdout
    pid, fd = None, ''
    for line in output.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('f'):
            fd = line[1:]
        elif line.startswith('n') and pid in found:
            path = line[1:]
            if fd == 'cwd':
                found[pid]['cwd'] = path
            elif path.endswith('.jsonl'):
                found[pid]['logs'].append(path)
    return found


def terminal_tabs(host_names=('Terminal',)):
    # Never launch Terminal to enumerate it. TTY makes tab identity unambiguous.
    script = '''if application "Terminal" is not running then return ""
      set rows to ""
      tell application "Terminal"
        repeat with w in windows
          repeat with t in tabs of w
            set rows to rows & (tty of t) & ASCII character 31 & (custom title of t) & linefeed
          end repeat
        end repeat
      end tell
      return rows'''
    iterm_script = '''if application "iTerm2" is not running then return ""
      set rows to ""
      tell application "iTerm2"
        repeat with w in windows
          repeat with t in tabs of w
            repeat with s in sessions of t
              set rows to rows & (tty of s) & ASCII character 31 & (name of s) & linefeed
            end repeat
          end repeat
        end repeat
      end tell
      return rows'''
    tabs = {}
    for host, program in [('Terminal', script), ('iTerm', iterm_script)]:
        if host not in host_names:
            continue
        try:
            result = run(['osascript', '-e', program], check=False, timeout=2)
            tabs.update(line.split(SEP, 1) for line in result.stdout.splitlines() if SEP in line)
        except (OSError, RuntimeError):
            pass
    return tabs


def screen(pane):
    if pane.get('host') not in ('Terminal', 'iTerm') or not pane.get('pane_tty'):
        return None
    if pane['host'] == 'Terminal':
        script = '''on run argv
          if application "Terminal" is not running then return ""
          tell application "Terminal"
            repeat with w in windows
              repeat with t in tabs of w
                if tty of t is item 1 of argv then return contents of t
              end repeat
            end repeat
          end tell
          return ""
        end run'''
    else:
        script = '''on run argv
          if application "iTerm2" is not running then return ""
          tell application "iTerm2"
            repeat with w in windows
              repeat with t in tabs of w
                repeat with s in sessions of t
                  if tty of s is item 1 of argv then return text of s
                end repeat
              end repeat
            end repeat
          end tell
          return ""
        end run'''
    result = run(['osascript', '-e', script, pane['pane_tty']], check=False, timeout=2)
    return result.stdout if result.returncode == 0 else None
